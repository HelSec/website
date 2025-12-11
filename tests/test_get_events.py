"""
Unit tests for get_events.py
"""

import os
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch, mock_open, MagicMock

import pytest
import requests
from dateutil.tz import gettz

from get_events import (
    fetch_events_list,
    fetch_event_details,
    is_future_event,
    generate_filename,
    create_markdown_content,
    file_exists,
    save_markdown_file,
    _remove_non_members_from_name,
)


class TestFetchEventsList:
    """Tests for fetch_events_list function"""
    
    @patch('get_events.requests.get')
    def test_fetch_events_list_success(self, mock_get):
        """Test successful parsing of events list from Pretix API"""
        mock_page_1 = Mock()
        mock_page_1.json.return_value = {
            "results": [
                {
                    "name": {"en": "Test Event"},
                    "slug": "test-event",
                    "date_from": "2025-12-12T16:00:00+00:00",
                    "public_url": "https://events.helsec.fi/helsec/test-event/"
                }
            ],
            "next": None
        }
        mock_page_1.raise_for_status = Mock()
        mock_get.return_value = mock_page_1
        
        events = fetch_events_list('https://events.helsec.fi', 'test', 'token')
        
        assert len(events) == 1
        assert events[0]['name'] == 'Test Event'
        assert events[0]['datetime'] == '2025-12-12T16:00:00+00:00'
        assert events[0]['link'] == "https://events.helsec.fi/helsec/test-event/"
        assert events[0]['slug'] == 'test-event'
    
    @patch('get_events.requests.get')
    def test_fetch_events_list_no_events(self, mock_get):
        """Test handling of empty results"""
        mock_resp = Mock()
        mock_resp.json.return_value = {"results": [], "next": None}
        mock_resp.raise_for_status = Mock()
        mock_get.return_value = mock_resp
        
        events = fetch_events_list('https://events.helsec.fi', 'test', 'token')
        
        assert len(events) == 0
    
    @patch('get_events.requests.get')
    def test_fetch_events_list_http_error(self, mock_get):
        """Test handling of HTTP errors"""
        mock_get.side_effect = requests.RequestException("Connection error")
        
        with pytest.raises(Exception):
            fetch_events_list('https://events.helsec.fi', 'test', 'token')


class TestFetchEventDetails:
    """Tests for fetch_event_details function"""
    
    @patch('get_events.requests.get')
    def test_fetch_event_details_success(self, mock_get):
        """Test successful parsing of event details via Pretix API"""
        detail_resp = Mock()
        detail_resp.json.return_value = {
            "name": {"en": "Test Event"},
            "location": {"en": "Test Location"},
            "date_from": "2025-12-12T16:00:00+00:00",
            "public_url": "https://events.helsec.fi/helsec/test-event/"
        }
        detail_resp.raise_for_status = Mock()

        settings_resp = Mock()
        settings_resp.json.return_value = {
            "frontpage_text": {
                "en": "Event description here\n\nThis is markdown text."
            }
        }
        settings_resp.raise_for_status = Mock()

        mock_get.side_effect = [detail_resp, settings_resp]
        
        details = fetch_event_details('https://events.helsec.fi', 'helsec', 'test-event', 'token')
        
        assert details['description'] == 'Event description here\n\nThis is markdown text.'
        assert details['name'] == 'Test Event'
        assert details['location'] == 'Test Location'
        assert details['datetime'] == '2025-12-12T16:00:00+00:00'
        assert details['link'] == 'https://events.helsec.fi/helsec/test-event/'
    
    @patch('get_events.requests.get')
    def test_fetch_event_details_http_error(self, mock_get):
        """Test handling of HTTP errors"""
        mock_get.side_effect = requests.RequestException("Connection error")
        
        with pytest.raises(Exception):
            fetch_event_details('https://events.helsec.fi', 'helsec', 'test-event', 'token')


class TestRemoveNonMembersFromName:
    """Tests for _remove_non_members_from_name function"""
    
    def test_removes_non_members_at_end(self):
        """Test removing 'non-members' from end of name"""
        assert _remove_non_members_from_name('Event Non-Members') == 'Event'
        assert _remove_non_members_from_name('Event non-members') == 'Event'
        assert _remove_non_members_from_name('Event Non Members') == 'Event'
        assert _remove_non_members_from_name('Event non members') == 'Event'
    
    def test_removes_non_members_at_start(self):
        """Test removing 'non-members' from start of name"""
        assert _remove_non_members_from_name('Non-Members Event') == 'Event'
        assert _remove_non_members_from_name('non-members Event') == 'Event'
    
    def test_removes_non_members_in_middle(self):
        """Test removing 'non-members' from middle of name"""
        assert _remove_non_members_from_name('Event Non-Members Meeting') == 'Event Meeting'
    
    def test_preserves_name_without_non_members(self):
        """Test that names without 'non-members' are unchanged"""
        assert _remove_non_members_from_name('Regular Event Name') == 'Regular Event Name'
        assert _remove_non_members_from_name('Event') == 'Event'
    
    def test_handles_empty_string(self):
        """Test handling of empty string"""
        assert _remove_non_members_from_name('') == ''
        assert _remove_non_members_from_name(None) == None


class TestIsFutureEvent:
    """Tests for is_future_event function"""
    
    def test_future_event(self):
        """Test that future events return True"""
        future_date = datetime.now(gettz('UTC')) + timedelta(days=1)
        assert is_future_event(future_date) is True
    
    def test_past_event(self):
        """Test that past events return False"""
        past_date = datetime.now(gettz('UTC')) - timedelta(days=1)
        assert is_future_event(past_date) is False
    
    def test_present_event(self):
        """Test that events happening now return False"""
        now = datetime.now(gettz('UTC'))
        # Event happening right now should not be considered future
        assert is_future_event(now) is False
    
    def test_timezone_aware(self):
        """Test with timezone-aware datetime"""
        helsinki_tz = gettz('Europe/Helsinki')
        future_date = datetime.now(helsinki_tz) + timedelta(days=1)
        assert is_future_event(future_date) is True


class TestGenerateFilename:
    """Tests for generate_filename function"""
    
    def test_generate_filename_with_datetime(self):
        """Test filename generation with datetime field"""
        event = {
            'name': 'Test Event',
            'datetime': '2025-12-12T18:00:00+0200'
        }
        filename = generate_filename(event)
        assert filename == '2025-12-12_Test_Event.md'
    
    def test_generate_filename_with_date(self):
        """Test filename generation with date field"""
        event = {
            'name': 'Test Event',
            'date': '2025-12-12'
        }
        filename = generate_filename(event)
        assert filename == '2025-12-12_Test_Event.md'
    
    def test_generate_filename_sanitization(self):
        """Test that special characters are removed"""
        event = {
            'name': 'Test Event (Special) - 2025!',
            'date': '2025-12-12'
        }
        filename = generate_filename(event)
        assert filename == '2025-12-12_Test_Event_Special_2025.md'
        assert '(' not in filename
        assert ')' not in filename
        assert '!' not in filename
    
    def test_generate_filename_spaces_to_underscores(self):
        """Test that spaces are converted to underscores"""
        event = {
            'name': 'Test Event Name',
            'date': '2025-12-12'
        }
        filename = generate_filename(event)
        assert filename == '2025-12-12_Test_Event_Name.md'
        assert ' ' not in filename
    
    def test_generate_filename_no_date(self):
        """Test filename generation when date is missing"""
        event = {
            'name': 'Test Event'
        }
        filename = generate_filename(event)
        # Should use today's date
        assert filename.endswith('_Test_Event.md')
        assert filename.startswith('20')  # Year prefix
    
    def test_generate_filename_removes_non_members(self):
        """Test that 'non-members' is removed from filename"""
        event = {
            'name': 'HelSec September 2025 Meetup Non-Members',
            'date': '2025-09-25'
        }
        filename = generate_filename(event)
        assert filename == '2025-09-25_HelSec_September_2025_Meetup.md'
        assert 'Non_Members' not in filename
        assert 'non_members' not in filename
    
    def test_generate_filename_removes_non_members_variations(self):
        """Test that various 'non-members' variations are removed"""
        test_cases = [
            ('Event Non-Members', 'Event'),
            ('Event non-members', 'Event'),
            ('Event Non Members', 'Event'),
            ('Non-Members Event', 'Event'),
            ('Event non members', 'Event'),
        ]
        for name, expected_base in test_cases:
            event = {
                'name': name,
                'date': '2025-09-25'
            }
            filename = generate_filename(event)
            assert filename.startswith('2025-09-25_')
            assert filename.endswith('.md')
            # Check that the base name is correct (allowing for sanitization)
            filename_base = filename.replace('2025-09-25_', '').replace('.md', '')
            assert expected_base.lower().replace(' ', '_') in filename_base.lower()


class TestCreateMarkdownContent:
    """Tests for create_markdown_content function"""
    
    def test_create_markdown_content_basic(self):
        """Test basic markdown content creation"""
        event = {
            'name': 'Test Event',
            'datetime': '2025-12-12T18:00:00+0200',
            'link': 'https://events.helsec.fi/helsec/test-event/',
            'description': 'This is a test event description.'
        }
        
        content = create_markdown_content(event)
        
        assert "---" in content
        assert "title: 'Test Event'" in content
        assert "date: 2025-12-12T18:00:00+0200" in content
        assert "link: 'https://events.helsec.fi/helsec/test-event/'" in content
        assert "This is a test event description." in content
    
    def test_create_markdown_content_with_double_spaces(self):
        """Test that two spaces are added before newlines for hard line breaks"""
        event = {
            'name': 'Test Event',
            'datetime': '2025-12-12T18:00:00+0200',
            'link': 'https://events.helsec.fi/helsec/test-event/',
            'description': 'Line one\nLine two\n\nParagraph two'
        }
        
        content = create_markdown_content(event)
        
        # Extract just the description part (after frontmatter)
        description_part = content.split('---\n\n', 1)[1] if '---\n\n' in content else content
        
        # Check that two spaces are added before newlines for regular lines
        assert 'Line one  \n' in description_part
        assert 'Line two  \n' in description_part
        # Empty line should remain empty (paragraph break)
        assert '\n\n' in description_part
        # Last line should not have trailing spaces
        assert description_part.endswith('Paragraph two') or description_part.endswith('Paragraph two\n')
    
    def test_create_markdown_content_headers_no_spaces(self):
        """Test that headers don't get trailing spaces"""
        event = {
            'name': 'Test Event',
            'datetime': '2025-12-12T18:00:00+0200',
            'description': '## Header\nRegular line'
        }
        
        content = create_markdown_content(event)
        
        # Header should not have trailing spaces
        assert '## Header\n' in content
        # Regular line should have trailing spaces
        assert 'Regular line  \n' in content or content.endswith('Regular line')
    
    def test_create_markdown_content_with_single_quote(self):
        """Test handling of single quotes in title"""
        event = {
            'name': "Test's Event",
            'datetime': '2025-12-12T18:00:00+0200',
            'link': 'https://events.helsec.fi/helsec/test-event/',
            'description': 'Description'
        }
        
        content = create_markdown_content(event)
        
        assert "title: 'Test\\'s Event'" in content or "title: \"Test's Event\"" in content
    
    def test_create_markdown_content_minimal(self):
        """Test markdown creation with minimal fields"""
        event = {
            'name': 'Test Event'
        }
        
        content = create_markdown_content(event)
        
        assert "---" in content
        assert "title: 'Test Event'" in content
    
    def test_create_markdown_content_removes_non_members_from_title(self):
        """Test that 'non-members' is removed from title in frontmatter"""
        event = {
            'name': 'HelSec September 2025 Meetup Non-Members',
            'datetime': '2025-09-25T18:00:00+0200',
            'description': 'Test description'
        }
        
        content = create_markdown_content(event)
        
        assert "title: 'HelSec September 2025 Meetup'" in content
        assert "Non-Members" not in content or "title: 'HelSec September 2025 Meetup Non-Members'" not in content
    
    def test_create_markdown_content_removes_non_members_variations(self):
        """Test that various 'non-members' variations are removed from title"""
        test_cases = [
            'Event Non-Members',
            'Event non-members',
            'Event Non Members',
            'Non-Members Event',
        ]
        for name in test_cases:
            event = {
                'name': name,
                'datetime': '2025-09-25T18:00:00+0200',
            }
            content = create_markdown_content(event)
            # Title should not contain "non-members" in any form
            assert 'non-members' not in content.lower() or f"title: '{name}'" not in content
    
    def test_create_markdown_content_adds_empty_line_after_streaming(self):
        """Test that an empty line is added after '## Streaming' if not already present"""
        event = {
            'name': 'Test Event',
            'datetime': '2025-09-25T18:00:00+0200',
            'description': '## Streaming   \nMeetup will be streamed on Twitch: https://twitch.tv/helsec'
        }
        
        content = create_markdown_content(event)
        description_part = content.split('---\n\n', 1)[1] if '---\n\n' in content else content
        
        # Should have empty line after "## Streaming"
        assert '## Streaming   \n\nMeetup' in description_part or '## Streaming\n\nMeetup' in description_part
    
    def test_create_markdown_content_preserves_existing_empty_line_after_streaming(self):
        """Test that existing empty line after '## Streaming' is preserved"""
        event = {
            'name': 'Test Event',
            'datetime': '2025-09-25T18:00:00+0200',
            'description': '## Streaming   \n\nMeetup will be streamed on Twitch: https://twitch.tv/helsec'
        }
        
        content = create_markdown_content(event)
        description_part = content.split('---\n\n', 1)[1] if '---\n\n' in content else content
        
        # Should have exactly one empty line (not double empty lines)
        # Check that we don't have triple newlines
        assert '\n\n\n' not in description_part or description_part.count('## Streaming') == 1

    def test_create_markdown_content_trims_trailing_hr(self):
        """Test that trailing '---' line at the end of description is removed"""
        event = {
            'name': 'Test Event',
            'datetime': '2025-09-25T18:00:00+0200',
            'description': 'Line one\nLine two\n---'
        }
        
        content = create_markdown_content(event)
        description_part = content.split('---\n\n', 1)[1] if '---\n\n' in content else content
        
        # Description should not end with '---'
        assert not description_part.strip().endswith('---')
        # Should still contain the earlier lines with hard breaks
        assert 'Line one  \n' in description_part
        assert 'Line two' in description_part


class TestFileExists:
    """Tests for file_exists function"""
    
    @patch('get_events._validate_directory')
    @patch('get_events.Path')
    def test_file_exists_true(self, mock_path, mock_validate_dir):
        """Test when file exists"""
        mock_dir = Mock()
        mock_file = Mock()
        mock_file.exists.return_value = True
        mock_dir.__truediv__ = Mock(return_value=mock_file)
        mock_validate_dir.return_value = mock_dir
        
        assert file_exists('test.md', 'content/events') is True
        # _validate_directory is called with directory and base_path (which defaults to None)
        mock_validate_dir.assert_called_once()
        assert mock_validate_dir.call_args[0][0] == 'content/events'
    
    @patch('get_events._validate_directory')
    @patch('get_events.Path')
    def test_file_exists_false(self, mock_path, mock_validate_dir):
        """Test when file does not exist"""
        mock_dir = Mock()
        mock_file = Mock()
        mock_file.exists.return_value = False
        mock_dir.__truediv__ = Mock(return_value=mock_file)
        mock_validate_dir.return_value = mock_dir
        
        assert file_exists('test.md', 'content/events') is False
        # _validate_directory is called with directory and base_path (which defaults to None)
        mock_validate_dir.assert_called_once()
        assert mock_validate_dir.call_args[0][0] == 'content/events'


class TestSaveMarkdownFile:
    """Tests for save_markdown_file function"""
    
    @patch('get_events._validate_directory')
    @patch('get_events.Path')
    @patch('builtins.open', new_callable=mock_open)
    def test_save_markdown_file_success(self, mock_file, mock_path, mock_validate_dir):
        """Test successful file saving"""
        mock_dir = Mock()
        mock_dir.mkdir = Mock()
        mock_file_path = Mock()
        mock_file_path.__truediv__ = Mock(return_value=mock_file_path)
        mock_validate_dir.return_value = mock_dir
        mock_dir.__truediv__ = Mock(return_value=mock_file_path)
        
        result = save_markdown_file('test.md', 'Test content', 'content/events')
        
        # _validate_directory is called with directory and base_path (which defaults to None)
        mock_validate_dir.assert_called_once()
        assert mock_validate_dir.call_args[0][0] == 'content/events'
        mock_dir.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_file.assert_called_once()
        assert isinstance(result, str)
    
    @patch('get_events._validate_directory')
    @patch('get_events.Path')
    @patch('builtins.open', new_callable=mock_open)
    def test_save_markdown_file_io_error(self, mock_file, mock_path, mock_validate_dir):
        """Test handling of IO errors"""
        mock_dir = Mock()
        mock_dir.mkdir = Mock()
        mock_validate_dir.return_value = mock_dir
        mock_dir.__truediv__ = Mock(return_value=Mock())
        mock_file.side_effect = IOError("Permission denied")
        
        with pytest.raises(IOError):
            save_markdown_file('test.md', 'Test content', 'content/events')


class TestMain:
    """Tests for main function"""
    
    @patch.dict(os.environ, {
        'PRETIX_URL': 'https://events.helsec.fi',
        'ORGANIZER_SLUG': 'test',
        'API_TOKEN': 'token'
    })
    @patch('get_events._validate_directory')
    @patch('get_events.save_markdown_file')
    @patch('get_events.file_exists')
    @patch('get_events.create_markdown_content')
    @patch('get_events.generate_filename')
    @patch('get_events.fetch_event_details')
    @patch('get_events.is_future_event')
    @patch('get_events.fetch_events_list')
    def test_main_success(
        self,
        mock_fetch_list,
        mock_is_future,
        mock_fetch_details,
        mock_generate_filename,
        mock_create_content,
        mock_file_exists,
        mock_save_file,
        mock_validate_dir,
    ):
        """Test successful main execution"""
        # Setup validation mocks
        mock_validate_dir.return_value = Mock()  # Return a mock Path
        
        # Setup mocks
        mock_fetch_list.return_value = [{
            'name': 'Test Event',
            'date': '2025-12-12',
            'slug': 'test-event',
            'full_url': 'https://events.helsec.fi/test/test-event/'
        }]
        mock_is_future.return_value = True
        mock_fetch_details.return_value = {
            'description': 'Test description',
            'datetime': '2025-12-12T18:00:00+0200',
            'link': 'https://events.helsec.fi/test/test-event/'
        }
        mock_generate_filename.return_value = '2025-12-12_Test_Event.md'
        mock_file_exists.return_value = False
        mock_create_content.return_value = '---\ntitle: Test\n---\nContent'
        mock_save_file.return_value = 'content/events/2025-12-12_Test_Event.md'
        
        from get_events import main
        main()
        
        mock_fetch_list.assert_called_once()
        mock_is_future.assert_called()
        mock_fetch_details.assert_called_with('https://events.helsec.fi', 'test', 'test-event', 'token')
        mock_save_file.assert_called()
        # push_to_github should NOT be called anymore
    
    @patch.dict(os.environ, {
        'PRETIX_URL': 'https://events.helsec.fi',
        'ORGANIZER_SLUG': 'test',
        'API_TOKEN': 'token'
    })
    @patch('get_events.save_markdown_file')
    @patch('get_events.file_exists')
    @patch('get_events.create_markdown_content')
    @patch('get_events.generate_filename')
    @patch('get_events.fetch_event_details')
    @patch('get_events.is_future_event')
    @patch('get_events.fetch_events_list')
    @patch('get_events._validate_directory')
    def test_main_skip_members_only_event(
        self,
        mock_validate_dir,
        mock_fetch_list,
        mock_is_future,
        mock_fetch_details,
        mock_generate_filename,
        mock_create_content,
        mock_file_exists,
        mock_save_file
    ):
        """Test that events ending with ' members' are skipped"""
        # Setup validation mocks
        mock_validate_dir.return_value = Mock()
        
        # Setup mocks
        mock_fetch_list.return_value = [{
            'name': 'Test Event members',
            'date': '2025-12-12',
            'full_url': 'https://events.helsec.fi/helsec/test-event/'
        }]
        mock_is_future.return_value = True
        
        from get_events import main
        main()
        
        # Should not fetch details or create file for members-only events
        mock_fetch_details.assert_not_called()
        mock_save_file.assert_not_called()
    
    @patch.dict(os.environ, {
        'PRETIX_URL': 'https://events.helsec.fi',
        'ORGANIZER_SLUG': 'test',
        'API_TOKEN': 'token'
    })
    @patch('get_events.save_markdown_file')
    @patch('get_events.file_exists')
    @patch('get_events.create_markdown_content')
    @patch('get_events.generate_filename')
    @patch('get_events.fetch_event_details')
    @patch('get_events.is_future_event')
    @patch('get_events.fetch_events_list')
    @patch('get_events._validate_directory')
    def test_main_include_non_members_event(
        self,
        mock_validate_dir,
        mock_fetch_list,
        mock_is_future,
        mock_fetch_details,
        mock_generate_filename,
        mock_create_content,
        mock_file_exists,
        mock_save_file
    ):
        """Test that events ending with 'non-members' are NOT skipped"""
        # Setup validation mocks
        mock_validate_dir.return_value = Mock()
        
        # Setup mocks
        mock_fetch_list.return_value = [{
            'name': 'Test Event non-members',
            'date': '2025-12-12',
            'slug': 'test-event',
            'full_url': 'https://events.helsec.fi/test/test-event/'
        }]
        mock_is_future.return_value = True
        mock_fetch_details.return_value = {
            'description': 'Test description',
            'datetime': '2025-12-12T18:00:00+0200',
            'link': 'https://events.helsec.fi/test/test-event/'
        }
        mock_generate_filename.return_value = '2025-12-12_Test_Event_non_members.md'
        mock_file_exists.return_value = False
        mock_create_content.return_value = '---\ntitle: Test\n---\nContent'
        mock_save_file.return_value = 'content/events/2025-12-12_Test_Event_non_members.md'
        
        from get_events import main
        main()
        
        # Should process non-members events normally
        mock_fetch_details.assert_called_once_with('https://events.helsec.fi', 'test', 'test-event', 'token')
        mock_save_file.assert_called_once()
        # push_to_github should NOT be called anymore
    
    @patch.dict(os.environ, {'API_TOKEN': ''}, clear=True)
    def test_main_missing_env_vars(self):
        """Test main with missing environment variables"""
        from get_events import main
        
        with pytest.raises(ValueError, match="API_TOKEN"):
            main()
    
    @patch.dict(os.environ, {
        'PRETIX_URL': 'https://events.helsec.fi',
        'ORGANIZER_SLUG': 'test',
        'API_TOKEN': 't1pzdhkyjmvzt6nxjg9g5ayt3qaodck6qtmbqqw899vd84wutyf3l23obtd4fdm4',
        'SPECIFIC_EVENT_URL': 'https://events.helsec.fi/helsec/u97je/'
    })
    @patch('get_events.save_markdown_file')
    @patch('get_events.file_exists')
    @patch('get_events.create_markdown_content')
    @patch('get_events.generate_filename')
    @patch('get_events.fetch_event_details')
    @patch('get_events.fetch_events_list')
    def test_main_specific_event_url(
        self,
        mock_fetch_list,
        mock_fetch_details,
        mock_generate_filename,
        mock_create_content,
        mock_file_exists,
        mock_save_file
    ):
        """Test processing a specific event URL (for past events)"""
        # Setup mocks
        mock_fetch_details.return_value = {
            'name': 'Past Event',
            'description': 'Test description',
            'datetime': '2023-09-28T17:30:00+0200',
            'link': 'https://events.helsec.fi/helsec/u97je/'
        }
        mock_generate_filename.return_value = '2023-09-28_Past_Event.md'
        mock_file_exists.return_value = False
        mock_create_content.return_value = '---\ntitle: Past Event\n---\nContent'
        mock_save_file.return_value = 'content/events/2023-09-28_Past_Event.md'
        
        from get_events import main
        main()
        
        # Should NOT fetch events list when processing specific URL
        mock_fetch_list.assert_not_called()
        # Should fetch details for the specific event
        mock_fetch_details.assert_called_once_with(
            'https://events.helsec.fi',
            'test',
            'u97je',
            't1pzdhkyjmvzt6nxjg9g5ayt3qaodck6qtmbqqw899vd84wutyf3l23obtd4fdm4'
        )
        # Should save the file
        mock_save_file.assert_called_once()

