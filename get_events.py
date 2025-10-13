import requests
import re
import pytz
import os
from datetime import datetime
from markdownify import markdownify

EVENTS_DIR = "content/events/"

def create_event_file(name, data):
    with open(EVENTS_DIR + name, "w") as f:
        f.write(data)
    return

def is_dst(dt=None, timezone="UTC"):
    # https://stackoverflow.com/questions/2881025/python-daylight-savings-time :D
    if dt is None:
        dt = datetime.utcnow()
    timezone = pytz.timezone(timezone)
    timezone_aware_date = timezone.localize(dt, is_dst=None)
    return timezone_aware_date.tzinfo._dst.seconds != 0

def check_events():
    path, dirs, files = next(os.walk(EVENTS_DIR))
    count = len(files)
    return count-1 # minus _index.md

def main():
    url = "https://api.meetup.com/HelSec/events?scroll=future_or_past"
    try:
        r = requests.get(url)
    except requests.exceptions.RequestException as e:
        raise SystemExit(e)
    events_count = len(r.json())
    print("Events on meetup.com: {} and on disk: {}".format(events_count, check_events()))
    if events_count > check_events() or 1==1:
        print("Fetching new events from meetup.com")
        for event in r.json():
            print("Event: " + (event['name']))
            event_data = "---\n"
            event_data += "title: '{}'\n".format(event['name'])
            event_date = datetime.strptime("{} {}:00".format(event['local_date'], event['local_time']), '%Y-%m-%d %H:%M:%S')
            summertime = is_dst(event_date, "Europe/Helsinki")
            if summertime:
                timezone = "+0300"
            else:
                timezone = "+0200"
            event_data += "date: {}{}\n".format(str(event_date).replace(" ", "T"),timezone)
            if event['is_online_event'] == True:
                event_data += "stream: 'https://twitch.tv/helsec'\n"
            event_data += "link: '{}'\n".format(event['link'])
            event_data += "---\n\n"
            description = markdownify(event['description'])
            event_data += description
            name = "{}_{}.md".format(event['local_date'], re.sub(r"[^a-zA-Z0-9_]","",event['name'].replace(" ", "_")))
            create_event_file(name, event_data)
            #print(event_data)
    else:
        print("No new events on meetup.com")


if __name__ == "__main__":
    main()
