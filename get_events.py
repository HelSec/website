"""
Get events for events website.
Fetches events from Pretix API, filters for future events, and creates markdown files.
"""

import os
import re
import logging
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path
from urllib.parse import urlparse

import requests
from dateutil import parser as date_parser
from dateutil.tz import gettz

# Security constants
REQUEST_TIMEOUT = 30  # seconds
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _pretix_headers(api_token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Token {api_token}",
        "Accept": "application/json",
    }


def _get_english(value):
    """
    Extract English text from a multi-lingual dict.
    If the value is already a string, return it.
    Returns None if English is not available.
    """
    if isinstance(value, dict):
        return value.get("en")
    return value


def _remove_non_members_from_name(name: str) -> str:
    """
    Remove "non-members" (case-insensitive, with variations) from event name.
    
    Args:
        name: Event name string
        
    Returns:
        Name with "non-members" removed
    """
    if not name:
        return name
    
    # Remove "non-members" variations (case-insensitive)
    # Handle: "non-members", "Non-Members", "Non Members", "non members", etc.
    name = re.sub(r'\s*non[-\s]?members\s*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'^\s*non[-\s]?members\s*', '', name, flags=re.IGNORECASE)
    # Also handle if it appears in the middle (less common but possible)
    name = re.sub(r'\s+non[-\s]?members\s+', ' ', name, flags=re.IGNORECASE)
    
    return name.strip()


def _extract_slug_from_url(url: str) -> Optional[str]:
    """Extract the last non-empty path component as slug."""
    try:
        parsed = urlparse(url)
        parts = [p for p in parsed.path.split('/') if p]
        return parts[-1] if parts else None
    except Exception:
        return None


def _validate_directory(directory: str, base_path: Optional[str] = None) -> Path:
    """
    Validate and resolve directory path, preventing path traversal attacks.
    
    Args:
        directory: Directory path to validate
        base_path: Base path to resolve against (defaults to current working directory)
        
    Returns:
        Resolved Path object
        
    Raises:
        ValueError: If directory path is outside allowed base directory
    """
    if base_path is None:
        base_path = os.getcwd()
    
    base = Path(base_path).resolve()
    target = (base / directory).resolve()
    
    # Ensure target is within base directory
    try:
        target.relative_to(base)
    except ValueError:
        raise ValueError(f"Directory path '{directory}' resolves outside allowed base directory '{base_path}'")
    
    return target


def fetch_events_list(pretix_url: str, organizer_slug: str, api_token: str) -> List[Dict]:
    """
    Returns list of event dicts compatible with downstream processing.
    """
    base_url = f"{pretix_url}/api/v1/organizers/{organizer_slug}/events/"
    headers = _pretix_headers(api_token)
    params = {"is_public": "true", "live": "true", "ordering": "date_from"}

    events: List[Dict] = []
    next_url = base_url

    logger.info(f"Fetching events list from Pretix API: {base_url}")

    while next_url:
        try:
            resp = requests.get(next_url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("results", []):
                name = _get_english(item.get("name"))
                slug = item.get("slug")
                date_from = item.get("date_from")
                public_url = item.get("public_url") or (f"{pretix_url}/{organizer_slug}/{slug}/" if slug else None)

                events.append({
                    "name": name,
                    "datetime": date_from,
                    "date": date_from,
                    "link": public_url,
                    "full_url": public_url,
                    "slug": slug,
                })

            next_url = data.get("next")
            params = None  # use next pagination URL as-is
        except requests.RequestException as e:
            logger.error(f"Error fetching events list from Pretix: {e}")
            raise

    logger.info(f"Found {len(events)} events from Pretix API")
    return events


def fetch_event_details(pretix_url: str, organizer_slug: str, event_slug: str, api_token: str) -> Dict:
    """
    Fetch detailed information for a single event from Pretix API, including frontpage text.
    """
    detail_url = f"{pretix_url}/api/v1/organizers/{organizer_slug}/events/{event_slug}/"
    settings_url = f"{detail_url}settings/"
    headers = _pretix_headers(api_token)

    try:
        detail_resp = requests.get(detail_url, headers=headers, timeout=REQUEST_TIMEOUT)
        detail_resp.raise_for_status()
        detail = detail_resp.json()

        settings_resp = requests.get(settings_url, headers=headers, timeout=REQUEST_TIMEOUT)
        settings_resp.raise_for_status()
        settings = settings_resp.json()

        name = _get_english(detail.get("name"))
        location = _get_english(detail.get("location"))
        datetime_value = detail.get("date_from")
        link = detail.get("public_url") or f"{pretix_url}/{organizer_slug}/{event_slug}/"

        frontpage_text = settings.get("frontpage_text")
        description = ""
        if frontpage_text:
            # Extract English text from frontpage_text (multi-lingual dict)
            english_text = _get_english(frontpage_text)
            if english_text:
                # The API returns markdown directly, so use it as-is
                # Normalize line endings and clean up
                description = english_text.replace('\r\n', '\n').replace('\r', '\n').strip()

        return {
            "name": name,
            "description": description,
            "location": location,
            "datetime": datetime_value,
            "link": link,
            "full_url": link,
        }
    except requests.RequestException as e:
        logger.error(f"Error fetching event details for {event_slug}: {e}")
        raise


def is_future_event(event_date: datetime) -> bool:
    """
    Checks if an event date is in the future.
    
    Args:
        event_date: datetime object to check
        
    Returns:
        True if event is in the future, False otherwise
    """
    now = datetime.now(event_date.tzinfo if event_date.tzinfo else gettz('UTC'))
    return event_date > now


def generate_filename(event: Dict) -> str:
    """
    Generates a filename for an event markdown file.
    
    Format: YYYY-MM-DD_Event_name.md
    
    Args:
        event: Event dictionary with 'name' and 'date' or 'datetime' keys
        
    Returns:
        Filename string
    """
    # Get date from datetime or date field
    date_str = None
    if 'datetime' in event and event['datetime']:
        try:
            dt = date_parser.parse(event['datetime'])
            date_str = dt.strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            pass
    
    if not date_str and 'date' in event:
        try:
            dt = date_parser.parse(event['date'])
            date_str = dt.strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            date_str = event['date']  # Use as-is if parsing fails
    
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')
        logger.warning(f"Could not parse date for event {event.get('name', 'unknown')}, using today's date")
    
    # Sanitize event name
    name = event.get('name', 'Event')
    # Remove "non-members" from name
    name = _remove_non_members_from_name(name)
    # Remove special characters, replace spaces with underscores
    name = re.sub(r'[^\w\s-]', '', name)
    name = re.sub(r'[-\s]+', '_', name)
    name = name.strip('_')
    
    filename = f"{date_str}_{name}.md"
    logger.debug(f"Generated filename: {filename}")
    return filename


def create_markdown_content(event: Dict) -> str:
    """
    Creates markdown content for an event file.
    
    Args:
        event: Event dictionary with all necessary fields
        
    Returns:
        Markdown content string
    """
    # Extract title
    title = event.get('name', 'Event')
    # Remove "non-members" from title
    title = _remove_non_members_from_name(title)
    # Escape single quotes in title
    title = title.replace("'", "\\'")
    
    # Extract and format date
    date_str = None
    helsinki_tz = gettz('Europe/Helsinki')
    
    if 'datetime' in event and event['datetime']:
        try:
            dt = date_parser.parse(event['datetime'])
            # Convert to Helsinki timezone to match example format
            if dt.tzinfo:
                # Convert to Helsinki timezone
                dt = dt.astimezone(helsinki_tz)
            else:
                # Assume Europe/Helsinki timezone if not specified
                dt = dt.replace(tzinfo=helsinki_tz)
            
            # Format as ISO with timezone offset (e.g., 2025-12-12T18:00:00+0200)
            date_str = dt.strftime('%Y-%m-%dT%H:%M:%S%z')
            # Ensure format is +0200 not +02:00
            if len(date_str) > 19 and date_str[19] in ['+', '-']:
                if ':' in date_str[19:]:
                    date_str = date_str[:19] + date_str[19:].replace(':', '')
        except (ValueError, TypeError) as e:
            logger.warning(f"Error parsing datetime: {e}")
    
    if not date_str:
        # Fallback to date field
        if 'date' in event:
            try:
                dt = date_parser.parse(event['date'])
                if not dt.tzinfo:
                    dt = dt.replace(tzinfo=helsinki_tz)
                date_str = dt.strftime('%Y-%m-%dT%H:%M:%S%z')
            except (ValueError, TypeError):
                date_str = event.get('date', '')
    
    # Extract link
    link = event.get('link', event.get('full_url', ''))
    
    # Extract description
    description = event.get('description', '')
    
    # Add two spaces before each newline for markdown hard line breaks
    # (except for empty lines and headers which should remain as paragraph breaks)
    if description:
        lines = description.split('\n')
        processed_lines = []
        for i, line in enumerate(lines):
            # Don't add spaces to empty lines (preserve paragraph breaks)
            if not line.strip():
                processed_lines.append('')
            # Don't add spaces to headers (lines starting with #)
            elif line.strip().startswith('#'):
                # Preserve original line (with any leading whitespace)
                processed_lines.append(line)
                # Ensure empty line after "## Streaming" if not already present
                if line.strip().startswith('## Streaming'):
                    # Check if next line exists and is not empty (meaning we need to add empty line)
                    if i < len(lines) - 1 and lines[i + 1].strip():
                        processed_lines.append('')
            # Add two spaces before newline for all other lines (except the last one)
            else:
                if i < len(lines) - 1:
                    processed_lines.append(line + '  ')
                else:
                    processed_lines.append(line)

        # Trim trailing empties and trailing horizontal-rule lines ('---')
        while processed_lines and not processed_lines[-1].strip():
            processed_lines.pop()
        while processed_lines and processed_lines[-1].strip() == '---':
            processed_lines.pop()

        # Preserve a single trailing newline
        description = '\n'.join(processed_lines)
    
    # Build markdown content
    frontmatter = f"---\n"
    frontmatter += f"title: '{title}'\n"
    if date_str:
        frontmatter += f"date: {date_str}\n"
    if link:
        frontmatter += f"link: '{link}'\n"
    frontmatter += f"---\n\n"
    
    content = frontmatter + description
    
    return content


def file_exists(filename: str, directory: str) -> bool:
    """
    Checks if a file already exists in the specified directory.
    
    Args:
        filename: Name of the file to check
        directory: Directory path to check in
        
    Returns:
        True if file exists, False otherwise
    """
    # Validate directory path to prevent path traversal
    dir_path = _validate_directory(directory)
    
    # Sanitize filename to prevent path traversal
    # Remove any path separators from filename
    safe_filename = os.path.basename(filename)
    if safe_filename != filename:
        logger.warning(f"Filename contained path separators, sanitized: {filename} -> {safe_filename}")
    
    file_path = dir_path / safe_filename
    exists = file_path.exists()
    logger.debug(f"File {file_path} exists: {exists}")
    return exists


def save_markdown_file(filename: str, content: str, directory: str) -> str:
    """
    Saves markdown content to a file.
    
    Args:
        filename: Name of the file to create
        content: Markdown content to write
        directory: Directory to save the file in
        
    Returns:
        Full path to the created file
        
    Raises:
        ValueError: If content exceeds maximum file size
    """
    # Validate file size to prevent disk exhaustion
    content_size = len(content.encode('utf-8'))
    if content_size > MAX_FILE_SIZE:
        raise ValueError(f"Content size {content_size} bytes exceeds maximum allowed size of {MAX_FILE_SIZE} bytes")
    
    # Validate directory path to prevent path traversal
    dir_path = _validate_directory(directory)
    
    # Sanitize filename to prevent path traversal
    # Remove any path separators from filename
    safe_filename = os.path.basename(filename)
    if safe_filename != filename:
        logger.warning(f"Filename contained path separators, sanitized: {filename} -> {safe_filename}")
    
    # Create directory if it doesn't exist
    dir_path.mkdir(parents=True, exist_ok=True)
    
    file_path = dir_path / safe_filename
    
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"Saved markdown file: {file_path}")
        return str(file_path)
    except IOError as e:
        logger.error(f"Error saving file {file_path}: {e}")
        raise


def _process_single_event(
    event_slug: str,
    pretix_url: str,
    organizer_slug: str,
    api_token: str,
    events_directory: str,
    event: Optional[Dict] = None,
    skip_future_check: bool = False
) -> None:
    """
    Process a single event: fetch details, apply filters, and create markdown file.
    
    Args:
        event_url: URL of the event to process
        events_directory: Directory to save markdown files
        event: Optional event dictionary with basic info (name, date, etc.)
        skip_future_check: If True, skip the future event check (for past events)
    """
    try:
        # Fetch full event details
        details = fetch_event_details(pretix_url, organizer_slug, event_slug, api_token)
        
        # Merge with existing event info if provided
        if event:
            event.update(details)
            # Prefer datetime from details, then from event, then fallback to time/date
            event['datetime'] = details.get('datetime') or event.get('datetime') or event.get('time') or event.get('date')
        else:
            # Create event dict from details
            event = details
        
        # Parse event date
        event_date = None
        if 'datetime' in event and event['datetime']:
            try:
                event_date = date_parser.parse(event['datetime'])
            except (ValueError, TypeError):
                pass
        
        if not event_date and 'date' in event:
            try:
                event_date = date_parser.parse(event['date'])
                # Set timezone if not present (assume Europe/Helsinki)
                if not event_date.tzinfo:
                    event_date = event_date.replace(tzinfo=gettz('Europe/Helsinki'))
            except (ValueError, TypeError):
                logger.warning(f"Could not parse date for event: {event.get('name', 'unknown')}")
                return
        
        # Check if event is in the future (unless skip_future_check is True)
        if not skip_future_check:
            if not event_date:
                logger.warning(f"No valid date found for event: {event.get('name', 'unknown')}")
                return
            
            if not is_future_event(event_date):
                logger.info(f"Skipping past event: {event.get('name', 'unknown')}")
                return
        
        # Skip events whose title ends with " members" (with space)
        event_name = event.get('name', '')
        if event_name.endswith(' members'):
            logger.info(f"Skipping members-only event: {event_name}")
            return
        
        # Generate filename
        filename = generate_filename(event)
        
        # Check if file already exists
        if file_exists(filename, events_directory):
            logger.info(f"File already exists, skipping: {filename}")
            return
        
        # Create markdown content
        markdown_content = create_markdown_content(event)
        
        # Save file
        file_path = save_markdown_file(filename, markdown_content, events_directory)
        
        logger.info(f"Successfully processed event: {event.get('name', 'unknown')}")
        
    except Exception as e:
        logger.error(f"Error processing event {event.get('name', 'unknown') if event else 'unknown'}: {e}")
        raise


def main() -> None:
    """
    Main function that orchestrates the scraping and file creation process.
    """
    pretix_url = os.getenv("PRETIX_URL", "https://events.helsec.fi")
    organizer_slug = os.getenv("ORGANIZER_SLUG", "test")
    api_token = os.getenv("API_TOKEN")
    events_directory = os.getenv("EVENTS_DIRECTORY", "content/events")
    specific_event_slug = os.getenv("SPECIFIC_EVENT_SLUG")

    if not api_token:
        raise ValueError("API_TOKEN environment variable is not set")

    logger.info("Starting Pretix API event fetcher")

    try:
        # Process a specific event if provided (skip future check)
        if specific_event_slug:
            logger.info(f"Processing specific event slug: {specific_event_slug}")
            _process_single_event(
                specific_event_slug,
                pretix_url,
                organizer_slug,
                api_token,
                events_directory,
                skip_future_check=True,
            )
            logger.info("Event processing completed")
            return

        events = fetch_events_list(pretix_url, organizer_slug, api_token)

        if not events:
            logger.info("No events found")
            return

        for event in events:
            try:
                # Parse event date
                event_date = None
                if 'datetime' in event and event['datetime']:
                    try:
                        event_date = date_parser.parse(event['datetime'])
                    except (ValueError, TypeError):
                        pass
                
                if not event_date and 'date' in event:
                    try:
                        event_date = date_parser.parse(event['date'])
                        # Set timezone if not present (assume Europe/Helsinki)
                        if not event_date.tzinfo:
                            event_date = event_date.replace(tzinfo=gettz('Europe/Helsinki'))
                    except (ValueError, TypeError):
                        logger.warning(f"Could not parse date for event: {event.get('name', 'unknown')}")
                        continue
                
                if not event_date:
                    logger.warning(f"No valid date found for event: {event.get('name', 'unknown')}")
                    continue
                
                # Check if event is in the future
                if not is_future_event(event_date):
                    logger.info(f"Skipping past event: {event.get('name', 'unknown')}")
                    continue
                
                # Skip events whose title ends with " members" (with space)
                event_name = event.get('name', '')
                if event_name.endswith(' members'):
                    logger.info(f"Skipping members-only event: {event_name}")
                    continue

                slug = event.get("slug")
                if not slug:
                    logger.warning(f"No slug found for event: {event.get('name', 'unknown')}")
                    continue

                _process_single_event(
                    slug,
                    pretix_url,
                    organizer_slug,
                    api_token,
                    events_directory,
                    event=event,
                    skip_future_check=False,
                )
                
            except Exception as e:
                logger.error(f"Error processing event {event.get('name', 'unknown')}: {e}")
                continue
        
        logger.info("Event processing completed")
        
    except Exception as e:
        logger.error(f"Fatal error in main: {e}")
        raise


if __name__ == '__main__':
    main()

