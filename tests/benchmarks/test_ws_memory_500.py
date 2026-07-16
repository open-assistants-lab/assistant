"""WebSocket Memory Integration Test — 500 interactions with date-spaced history.

Strategy:
1. Pre-seed the message DB with ~200 messages across 30+ past dates
   (simulating a real user with months of conversation history)
2. Run 500 WS interactions through /ws/conversation
3. Validate recall accuracy, tool usage, protocol compliance, temporal reasoning

Date distribution for pre-seeded data:
  - 90 days ago: 20 messages (early facts, old job, old city)
  - 60 days ago: 25 messages (mid-period facts, preferences)
  - 30 days ago: 30 messages (recent facts, some updates)
  - 14 days ago: 25 messages (recent updates)
  - 7 days ago: 20 messages (very recent)
  - 3 days ago: 15 messages (latest context)
  - Today: 500 live WS interactions

Usage:
    uv run assistant http &
    uv run python tests/benchmarks/test_ws_memory_500.py
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import websockets
except ImportError:
    print("websockets not installed. Run: uv add websockets")
    raise

from src.storage.messages import get_message_store

WS_URL = "ws://localhost:8080/ws/conversation"
USER_ID = "ws_test_user"
TIMEOUT_PER_TURN = 90


@dataclass
class TurnResult:
    interaction_id: int
    category: str
    input_message: str
    expected_behavior: str
    got_done: bool = False
    got_error: bool = False
    got_text: bool = False
    got_tool_call: bool = False
    tool_names: list[str] = field(default_factory=list)
    response_text: str = ""
    event_types_received: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    validation_passed: bool = False
    validation_detail: str = ""


# ─── Pre-seeded messages with explicit dates ───

def _dt(days_ago: int, hour: int = 10) -> str:
    ts = datetime.now(UTC) - timedelta(days=days_ago, hours=24 - hour)
    return ts.isoformat()

SEED_DATA: list[dict] = []

# ── 90 days ago: Early facts ──
d90 = 90
SEED_DATA += [
    {"ts": _dt(d90, 9), "role": "user", "content": "Hi, I'm Jordan Mitchell. I just started using this assistant."},
    {"ts": _dt(d90, 9), "role": "assistant", "content": "Hello Jordan! Nice to meet you. How can I help?"},
    {"ts": _dt(d90, 10), "role": "user", "content": "I live in Austin, Texas. I work as a data analyst at Dell Technologies."},
    {"ts": _dt(d90, 10), "role": "assistant", "content": "Great to know, Jordan. I'll remember you're in Austin working as a data analyst at Dell."},
    {"ts": _dt(d90, 11), "role": "user", "content": "My email is jordan.mitchell@dell.com and my desk is in Building 4, Floor 2."},
    {"ts": _dt(d90, 14), "role": "user", "content": "I have a golden retriever named Biscuit. She's 3 years old."},
    {"ts": _dt(d90, 15), "role": "user", "content": "My wife's name is Priya. She's a physician at Austin General Hospital."},
    {"ts": _dt(d90, 16), "role": "user", "content": "I'm allergic to shellfish — found out the hard way at a company dinner."},
    {"ts": _dt(d90, 16), "role": "assistant", "content": "I'll note the shellfish allergy. That's important to remember."},
    {"ts": _dt(d90, 17), "role": "user", "content": "I prefer Python for data analysis, but I also use R sometimes."},
    {"ts": _dt(d90, 18), "role": "user", "content": "My favorite lunch spot is Torchy's Tacos on South Lamar."},
    {"ts": _dt(d90, 18), "role": "assistant", "content": "Torchy's is great! I'll remember that for lunch recommendations."},
    {"ts": _dt(d90, 19), "role": "user", "content": "I drive a 2021 Honda Civic. License plate JMD-4521."},
    {"ts": _dt(d90, 20), "role": "user", "content": "My birthday is June 22, 1992. My wife's birthday is November 3."},
    {"ts": _dt(d90, 20), "role": "assistant", "content": "I'll remember both birthdays: yours on June 22 and Priya's on November 3."},
    {"ts": _dt(d90, 21), "role": "user", "content": "I prefer dark roast coffee, black, no sugar."},
    {"ts": _dt(d90, 21), "role": "assistant", "content": "Noted — dark roast, black, no sugar. Classic!"},
    {"ts": _dt(d90, 22), "role": "user", "content": "My regular work hours are 8:30 AM to 5:30 PM Central."},
    {"ts": _dt(d90, 23), "role": "user", "content": "I take the MetroRail to work. The stop at Kramer Lane is closest to my office."},
    {"ts": _dt(d90, 23), "role": "assistant", "content": "Kramer Lane MetroRail stop — got it."},
    {"ts": _dt(d90, 23), "role": "user", "content": "My manager at Dell is Karen Walsh. She's pretty strict about deadlines."},
]

# ── 60 days ago: Mid-period facts ──
d60 = 60
SEED_DATA += [
    {"ts": _dt(d60, 9), "role": "user", "content": "Quick update — I got moved to the Analytics Platform team last week."},
    {"ts": _dt(d60, 9), "role": "assistant", "content": "Congrats on the new team! Analytics Platform — I'll update that."},
    {"ts": _dt(d60, 10), "role": "user", "content": "My new teammate Raj is really helpful. He sits in Building 4, Floor 3."},
    {"ts": _dt(d60, 11), "role": "user", "content": "I started learning Rust on the weekends. It's quite different from Python."},
    {"ts": _dt(d60, 11), "role": "assistant", "content": "Rust is a great language to learn — good systems programming skills."},
    {"ts": _dt(d60, 12), "role": "user", "content": "I prefer Zoom over Google Meet for meetings. The video quality is better."},
    {"ts": _dt(d60, 13), "role": "user", "content": "My current project is the Customer Churn Prediction dashboard."},
    {"ts": _dt(d60, 13), "role": "assistant", "content": "Customer Churn Prediction dashboard — noted as your current project."},
    {"ts": _dt(d60, 14), "role": "user", "content": "I usually work from home on Fridays. It's my deep focus day."},
    {"ts": _dt(d60, 14), "role": "assistant", "content": "WFH Fridays for deep focus — great habit."},
    {"ts": _dt(d60, 15), "role": "user", "content": "I have a dentist appointment at Austin Dental on 5th Street. Dr. Martinez."},
    {"ts": _dt(d60, 16), "role": "user", "content": "My dad's name is Robert Mitchell. He lives in Houston."},
    {"ts": _dt(d60, 16), "role": "assistant", "content": "Robert Mitchell in Houston — noted."},
    {"ts": _dt(d60, 17), "role": "user", "content": "I prefer Jira for project tracking. Not a fan of Asana."},
    {"ts": _dt(d60, 17), "role": "assistant", "content": "Jira over Asana — preference noted."},
    {"ts": _dt(d60, 18), "role": "user", "content": "My VPN access code for Dell is DM-8823. Don't share that."},
    {"ts": _dt(d60, 18), "role": "assistant", "content": "VPN code DM-8823 kept private. Understood."},
    {"ts": _dt(d60, 19), "role": "user", "content": "I got a new monitor — 34-inch ultrawide from Dell. It's amazing for data work."},
    {"ts": _dt(d60, 19), "role": "assistant", "content": "A 34-inch ultrawide — perfect for spreadsheets and dashboards."},
    {"ts": _dt(d60, 20), "role": "user", "content": "My gym membership is at Lifetime Fitness on South Congress. I go M/W/F at 6am."},
    {"ts": _dt(d60, 20), "role": "assistant", "content": "Lifetime Fitness, South Congress, M/W/F at 6am. Discipline!"},
    {"ts": _dt(d60, 21), "role": "user", "content": "I have a 401k through Dell, contributing 8%. They match up to 5%."},
    {"ts": _dt(d60, 21), "role": "assistant", "content": "8% with 5% match — solid retirement planning."},
    {"ts": _dt(d60, 22), "role": "user", "content": "My best friend from college is Tyler. He works at Samsung in Austin too."},
    {"ts": _dt(d60, 22), "role": "assistant", "content": "Tyler at Samsung — good to have friends nearby at work."},
    {"ts": _dt(d60, 22), "role": "user", "content": "I read mostly sci-fi. My favorite author is Adrian Tchaikovsky."},
    {"ts": _dt(d60, 22), "role": "assistant", "content": "Adrian Tchaikovsky — great choice! Children of Time is a masterpiece."},
    {"ts": _dt(d60, 23), "role": "user", "content": "I prefer Spotify over Apple Music. My playlist is mostly indie rock."},
]

# ── 30 days ago: Recent facts + some updates ──
d30 = 30
SEED_DATA += [
    {"ts": _dt(d30, 9), "role": "user", "content": "I got a promotion! I'm now a Senior Data Analyst on the Platform team."},
    {"ts": _dt(d30, 9), "role": "assistant", "content": "Congratulations on the promotion to Senior Data Analyst! Well deserved."},
    {"ts": _dt(d30, 10), "role": "user", "content": "My new salary is $125,000. I'm pretty happy about it."},
    {"ts": _dt(d30, 10), "role": "assistant", "content": "$125K — that's a good step up. Happy for you."},
    {"ts": _dt(d30, 11), "role": "user", "content": "Actually, I stopped drinking coffee. Switched to green tea about a month ago."},
    {"ts": _dt(d30, 11), "role": "assistant", "content": "Noted — you switched from dark roast coffee to green tea. That's a change!"},
    {"ts": _dt(d30, 12), "role": "user", "content": "My new project is the Revenue Forecasting Engine. The churn dashboard got handed off."},
    {"ts": _dt(d30, 12), "role": "assistant", "content": "Revenue Forecasting Engine — exciting new project."},
    {"ts": _dt(d30, 13), "role": "user", "content": "Priya and I are planning a trip to Japan in October. We're going for 2 weeks."},
    {"ts": _dt(d30, 13), "role": "assistant", "content": "Japan in October — 2 weeks! That'll be amazing. Cherry blossom season... well, autumn foliage."},
    {"ts": _dt(d30, 14), "role": "user", "content": "I need to renew my passport by August. It expires in September."},
    {"ts": _dt(d30, 14), "role": "assistant", "content": "I'll remind you about passport renewal. Deadline is before August."},
    {"ts": _dt(d30, 15), "role": "user", "content": "I joined a book club at work. We meet the first Wednesday of every month."},
    {"ts": _dt(d30, 15), "role": "assistant", "content": "Book club, first Wednesday monthly — fun! What are you reading?"},
    {"ts": _dt(d30, 16), "role": "user", "content": "We're reading 'Project Hail Mary' by Andy Weir right now. It's fantastic."},
    {"ts": _dt(d30, 16), "role": "assistant", "content": "Project Hail Mary is excellent! Andy Weir at his best."},
    {"ts": _dt(d30, 17), "role": "user", "content": "I started using Obsidian for notes instead of Notion. I like the local-first approach."},
    {"ts": _dt(d30, 17), "role": "assistant", "content": "Obsidian is great for local-first. Markdown-based too."},
    {"ts": _dt(d30, 18), "role": "user", "content": "My parking spot at Dell garage changed from G-12 to G-28."},
    {"ts": _dt(d30, 18), "role": "assistant", "content": "Parking spot updated: G-12 → G-28."},
    {"ts": _dt(d30, 19), "role": "user", "content": "I've been having back pain. My doctor recommended a standing desk."},
    {"ts": _dt(d30, 19), "role": "assistant", "content": "Sorry about the back pain. A standing desk should help."},
    {"ts": _dt(d30, 20), "role": "user", "content": "I ordered a standing desk from Uplift. It should arrive next week."},
    {"ts": _dt(d30, 20), "role": "assistant", "content": "Uplift is a solid brand. Hope it helps with the back pain."},
    {"ts": _dt(d30, 21), "role": "user", "content": "Actually, I don't prefer dark mode anymore. I switched my editors to light theme."},
    {"ts": _dt(d30, 21), "role": "assistant", "content": "Light theme it is! Preference updated from dark mode."},
    {"ts": _dt(d30, 22), "role": "user", "content": "My mom's birthday is April 8. I need to send her flowers from 1-800-Flowers."},
    {"ts": _dt(d30, 22), "role": "assistant", "content": "Mom's birthday April 8 — flowers from 1-800-Flowers. I'll remember."},
    {"ts": _dt(d30, 23), "role": "user", "content": "I signed up for a half marathon in December. The Austin Half on December 14."},
    {"ts": _dt(d30, 23), "role": "assistant", "content": "Austin Half Marathon on Dec 14 — ambitious goal! Need a training plan?"},
]

# ── 14 days ago: Updates ──
d14 = 14
SEED_DATA += [
    {"ts": _dt(d14, 9), "role": "user", "content": "I got a new phone! iPhone 16 Pro. The camera is incredible."},
    {"ts": _dt(d14, 9), "role": "assistant", "content": "iPhone 16 Pro — nice upgrade!"},
    {"ts": _dt(d14, 10), "role": "user", "content": "My new phone number is 512-555-8899. The old one is deactivated."},
    {"ts": _dt(d14, 10), "role": "assistant", "content": "Phone updated: 512-555-8899. Old number deactivated."},
    {"ts": _dt(d14, 11), "role": "user", "content": "Actually, my standing desk arrived and it's set up. My back already feels better."},
    {"ts": _dt(d14, 11), "role": "assistant", "content": "Great to hear the standing desk is helping!"},
    {"ts": _dt(d14, 12), "role": "user", "content": "I switched from Jira to Linear for project tracking. The UI is so much cleaner."},
    {"ts": _dt(d14, 12), "role": "assistant", "content": "Linear over Jira — that's a switch. Noted."},
    {"ts": _dt(d14, 13), "role": "user", "content": "Raj left the team. He got transferred to the Security team. I'm sad about it."},
    {"ts": _dt(d14, 13), "role": "assistant", "content": "Sorry to hear Raj moved on. At least he's still at Dell."},
    {"ts": _dt(d14, 14), "role": "user", "content": "My new teammate is Lisa Chen. She just moved from the Seattle office."},
    {"ts": _dt(d14, 14), "role": "assistant", "content": "Lisa Chen from Seattle — welcome to Austin!"},
    {"ts": _dt(d14, 15), "role": "user", "content": "I'm no longer taking the MetroRail. I switched to biking to work. 6 miles each way."},
    {"ts": _dt(d14, 15), "role": "assistant", "content": "Biking 6 miles — great exercise! No more MetroRail."},
    {"ts": _dt(d14, 16), "role": "user", "content": "I cancelled my Lifetime Fitness membership since I'm biking now."},
    {"ts": _dt(d14, 16), "role": "assistant", "content": "Gym membership cancelled — biking is your workout now."},
    {"ts": _dt(d14, 17), "role": "user", "content": "Biscuit hurt her paw at the dog park. Vet says 2 weeks rest."},
    {"ts": _dt(d14, 17), "role": "assistant", "content": "Oh no, poor Biscuit! 2 weeks of rest — hopefully a quick recovery."},
    {"ts": _dt(d14, 18), "role": "user", "content": "My current doctor is Dr. Sarah Chen at Austin Family Medicine on Guadalupe St."},
    {"ts": _dt(d14, 18), "role": "assistant", "content": "Dr. Sarah Chen, Austin Family Medicine, Guadalupe St — noted."},
    {"ts": _dt(d14, 19), "role": "user", "content": "I'm now using Arc browser instead of Chrome. The workspaces feature is great."},
    {"ts": _dt(d14, 19), "role": "assistant", "content": "Arc browser — the workspaces are indeed handy for context switching."},
    {"ts": _dt(d14, 20), "role": "user", "content": "I renewed my passport! It arrived last week. Valid for 10 years now."},
    {"ts": _dt(d14, 20), "role": "assistant", "content": "Passport renewed — Japan trip is all set!"},
    {"ts": _dt(d14, 21), "role": "user", "content": "I started meal prepping on Sundays. Saves time and money."},
    {"ts": _dt(d14, 21), "role": "assistant", "content": "Sunday meal prep — efficient and budget-friendly."},
    {"ts": _dt(d14, 22), "role": "user", "content": "I paid off my student loans this month! Finally free of that debt."},
    {"ts": _dt(d14, 22), "role": "assistant", "content": "Student loans paid off — huge milestone! Congratulations!"},
    {"ts": _dt(d14, 23), "role": "user", "content": "My emergency contact is now Priya Mitchell at 512-555-8899."},
]

# ── 7 days ago: Very recent ──
d7 = 7
SEED_DATA += [
    {"ts": _dt(d7, 9), "role": "user", "content": "I got moved to a new manager — Tom Bradley. Karen Walsh retired last Friday."},
    {"ts": _dt(d7, 9), "role": "assistant", "content": "Tom Bradley as new manager. Karen Walsh retired — end of an era."},
    {"ts": _dt(d7, 10), "role": "user", "content": "My project changed again. I'm now on the Real-Time Analytics Pipeline."},
    {"ts": _dt(d7, 10), "role": "assistant", "content": "Real-Time Analytics Pipeline — sounds like a high-visibility project."},
    {"ts": _dt(d7, 11), "role": "user", "content": "I bought a new bike — Trek Domane AL 5. Carbon fork, aluminum frame."},
    {"ts": _dt(d7, 11), "role": "assistant", "content": "Trek Domane AL 5 — solid endurance road bike for your commute."},
    {"ts": _dt(d7, 12), "role": "user", "content": "I don't use VS Code anymore. I switched to Neovim with LazyVim config."},
    {"ts": _dt(d7, 12), "role": "assistant", "content": "Neovim + LazyVim — keyboard-driven workflow! Nice."},
    {"ts": _dt(d7, 13), "role": "user", "content": "I moved my parking spot again. It's H-09 now, closer to the bike rack."},
    {"ts": _dt(d7, 13), "role": "assistant", "content": "Parking H-09 near the bike rack — practical."},
    {"ts": _dt(d7, 14), "role": "user", "content": "Priya got a promotion too — she's now Chief of Emergency Medicine at Austin General."},
    {"ts": _dt(d7, 14), "role": "assistant", "content": "Chief of Emergency Medicine — that's an incredible achievement for Priya!"},
    {"ts": _dt(d7, 15), "role": "user", "content": "We decided to postpone the Japan trip to March next year. Too much going on."},
    {"ts": _dt(d7, 15), "role": "assistant", "content": "Japan trip postponed to March — makes sense with all the changes."},
    {"ts": _dt(d7, 16), "role": "user", "content": "I started a coding YouTube channel. It's called 'Data With Jordan'."},
    {"ts": _dt(d7, 16), "role": "assistant", "content": "Data With Jordan — nice channel name! What's your first video about?"},
    {"ts": _dt(d7, 17), "role": "user", "content": "My first video was about building a real-time data pipeline with Kafka and Python."},
    {"ts": _dt(d7, 17), "role": "assistant", "content": "Kafka + Python pipeline — great first video topic!"},
    {"ts": _dt(d7, 18), "role": "user", "content": "I prefer to be called 'JD' by friends and close colleagues. Not Jordan."},
    {"ts": _dt(d7, 18), "role": "assistant", "content": "JD it is! I'll use that for informal contexts."},
]

# ── 3 days ago: Latest ──
d3 = 3
SEED_DATA += [
    {"ts": _dt(d3, 9), "role": "user", "content": "Biscuit's paw is fully healed! She's back to running at the dog park."},
    {"ts": _dt(d3, 9), "role": "assistant", "content": "Great news about Biscuit! Happy running."},
    {"ts": _dt(d3, 10), "role": "user", "content": "I'm now officially a team lead for the Pipeline squad. 4 direct reports."},
    {"ts": _dt(d3, 10), "role": "assistant", "content": "Team lead with 4 reports — congrats on the leadership role!"},
    {"ts": _dt(d3, 11), "role": "user", "content": "My direct reports are Lisa Chen, Marco Reyes, Aisha Patel, and Dev Sharma."},
    {"ts": _dt(d3, 11), "role": "assistant", "content": "Lisa, Marco, Aisha, and Dev — strong team!"},
    {"ts": _dt(d3, 14), "role": "user", "content": "I signed up for a cloud certification — AWS Solutions Architect Associate."},
    {"ts": _dt(d3, 14), "role": "assistant", "content": "AWS SAA certification — great career move."},
    {"ts": _dt(d3, 16), "role": "user", "content": "I decided to stop drinking green tea. I'm back on coffee — but now I prefer oat milk lattes."},
    {"ts": _dt(d3, 16), "role": "assistant", "content": "Oat milk lattes — a real upgrade! Coffee drinkers always come back."},
    {"ts": _dt(d3, 17), "role": "user", "content": "My YouTube channel hit 100 subscribers today! Small but meaningful milestone."},
    {"ts": _dt(d3, 17), "role": "assistant", "content": "100 subscribers — everyone starts somewhere. Congrats!"},
    {"ts": _dt(d3, 19), "role": "user", "content": "I switched my 401k contribution to 10%. The 5% match still applies."},
    {"ts": _dt(d3, 19), "role": "assistant", "content": "10% contribution with 5% match — even better retirement planning."},
    {"ts": _dt(d3, 20), "role": "user", "content": "Priya and I adopted a kitten! Name is Noodle. She and Biscuit are getting along."},
    {"ts": _dt(d3, 20), "role": "assistant", "content": "Noodle the kitten — love that name! Glad Biscuit approves."},
    {"ts": _dt(d3, 21), "role": "user", "content": "I now use Notion again for personal stuff but Obsidian for work notes."},
    {"ts": _dt(d3, 21), "role": "assistant", "content": "Notion for personal, Obsidian for work — each has its strengths."},
    {"ts": _dt(d3, 22), "role": "user", "content": "My current address is 7422 Elm Creek Dr, Apt 3B, Austin TX 78745."},
]


def seed_messages() -> int:
    store = get_message_store(USER_ID)
    count = 0
    for row in SEED_DATA:
        store.db.insert(
            "messages",
            {
                "ts": row["ts"],
                "role": row["role"],
                "content": row["content"],
                "metadata": None,
            },
        )
        count += 1
    store.db._process_journal()
    return count


# ─── Live interactions (500) ───

def _build_interactions() -> list[dict[str, str]]:
    interactions: list[dict[str, str]] = []

    # Phase 1: Fact Recall — Pre-seeded data (1-80)
    recall_qs = [
        ("What is my name?", "jordan"), ("What do people call me informally?", "jd"),
        ("Where do I live?", "austin"), ("What is my address?", "7422"),
        ("Who do I work for?", "dell"), ("What is my job title?", "senior data analyst"),
        ("What is my wife's name?", "priya"), ("What is Priya's job?", "chief"),
        ("What is my dog's name?", "biscuit"), ("What kind of dog do I have?", "retriever"),
        ("Do I have a cat?", "noodle"), ("What's my cat's name?", "noodle"),
        ("Am I allergic to anything?", "shellfish"), ("What is my birthday?", "june"),
        ("When is Priya's birthday?", "november"), ("When is my mom's birthday?", "april"),
        ("What is my phone number?", "8899"), ("What is my email?", "jordan.mitchell"),
        ("What editor do I use now?", "neovim"), ("What browser do I use?", "arc"),
        ("What is my parking spot?", "h-09"), ("What building is my desk in?", "building 4"),
        ("Do I prefer tea or coffee?", "oat milk"), ("What kind of coffee do I drink?", "oat milk"),
        ("What is my current project?", "real-time"), ("What was my previous project?", "revenue"),
        ("Who is my manager?", "tom"), ("Who was my previous manager?", "karen"),
        ("How do I commute to work?", "bik"), ("What bike do I ride?", "trek"),
        ("Am I training for anything?", "half marathon"), ("When is the half marathon?", "december"),
        ("Do I have any certifications planned?", "aws"), ("What is my salary?", "125"),
        ("What is my 401k contribution?", "10"), ("What is my work schedule?", "8:30"),
        ("What day do I WFH?", "friday"), ("What book club book am I reading?", "hail mary"),
        ("Who is my doctor?", "sarah chen"), ("What is my dad's name?", "robert"),
        ("Who is my best friend?", "tyler"), ("Where does my dad live?", "houston"),
        ("What is my YouTube channel called?", "data with jordan"),
        ("Who are my direct reports?", "lisa"), ("What programming languages do I know?", "python"),
        ("Do I have any pets?", "biscuit"), ("What kind of car do I drive?", "civic"),
        ("What is my lunch spot?", "torchy"), ("Who is my emergency contact?", "priya"),
        ("What project tracking tool do I use?", "linear"), ("What note app do I use for work?", "obsidian"),
        ("What note app do I use for personal?", "notion"), ("What does my wife do?", "emergency"),
        ("How many direct reports do I have?", "4"), ("What is my Dell VPN code?", "8823"),
        ("What was my old parking spot?", "g-28"), ("Where do I go to the dentist?", "martinez"),
        ("What author do I like?", "tchaikovsky"), ("What genre do I read?", "sci-fi"),
        ("What music do I listen to?", "indie"), ("What streaming service do I use?", "spotify"),
        ("Did I pay off my student loans?", "paid"), ("Do I meal prep?", "sunday"),
        ("How many YouTube subscribers do I have?", "100"),
        ("What is my gym situation?", "bik"), ("What is my team called?", "pipeline"),
        ("What happened to my teammate Raj?", "security"), ("Who is my new teammate?", "lisa"),
        ("What is my first YouTube video about?", "kafka"), ("What is my office floor?", "floor 2"),
        ("When is my Japan trip?", "march"), ("What is my passport status?", "renew"),
        ("What happened to my dog's paw?", "heal"), ("Am I a team lead?", "team lead"),
        ("What is my license plate?", "4521"), ("What is my standing desk brand?", "uplift"),
        ("What monitor do I have?", "34"), ("What phone do I have?", "iphone 16"),
        ("What is my mom planning to get?", "flower"), ("When do I have book club?", "wednesday"),
        ("What is my weekend learning project?", "rust"),
    ]
    for q, v in recall_qs:
        interactions.append({"cat": "fact_recall", "msg": q, "expect": "recall from history", "validate": v})

    # Phase 2: Temporal Reasoning — across dates (81-150)
    temporal_qs = [
        ("What was my original job title at Dell?", "data analyst"),  # promoted to Senior
        ("What was my manager before Tom?", "karen"),  # Karen retired
        ("Where did I live when I first started using this assistant?", "austin"),  # hasn't moved
        ("What was my original parking spot?", "g-12"),  # changed to G-28, then H-09
        ("What did I drink when I first started here?", "dark roast"),  # coffee -> green tea -> oat milk lattes
        ("What editor did I use before Neovim?", "vs code"),  # switch history
        ("What project tracking tool did I use before Linear?", "jira"),
        ("How did I commute before biking?", "metrorail"),  # switched from MetroRail
        ("What was my project before the Pipeline?", "revenue"),  # Revenue Forecasting Engine
        ("Where was my gym before I cancelled it?", "lifetime"),  # Lifetime Fitness
        ("What was my old phone number?", "deactivated"),  # changed 14 days ago
        ("When did I switch from coffee to green tea?", "30"),  # ~30 days ago
        ("When did I start biking to work?", "14"),  # 14 days ago
        ("When did I get promoted to Senior Data Analyst?", "30"),  # 30 days ago
        ("When did Karen Walsh retire?", "7"),  # last Friday ~7 days
        ("When did I adopt Noodle?", "3"),  # 3 days ago
        ("What was my original team at Dell?", "analytics platform"),  # team changes
        ("What happened to my commute method over time?", "metrorail"),  # MetroRail -> biking
        ("How has my drink preference changed?", "coffee"),  # dark roast -> green tea -> oat milk
        ("What editor changes have I made?", "vs code"),  # VS Code -> Neovim
        ("When did I start using Obsidian?", "30"),  # ~30 days ago
        ("When did Biscuit hurt her paw?", "14"),  # 14 days ago
        ("When did I sign up for the half marathon?", "30"),  # 30 days ago
        ("When did I start my YouTube channel?", "7"),  # 7 days ago
        ("How has my parking spot changed?", "g-12"),  # G-12 -> G-28 -> H-09
        ("When did I buy my new bike?", "7"),  # 7 days ago
        ("What happened to my colleague Raj?", "security"),  # transferred
        ("When did I get my iPhone 16 Pro?", "14"),  # 14 days ago
        ("When did I become team lead?", "3"),  # 3 days ago
        ("How has my 401k changed?", "8"),  # 8% -> 10%
        ("What was my original 401k contribution?", "8"),  # started at 8%
        ("When did I switch to light theme?", "30"),  # 30 days ago
        ("What was my preference for dark vs light mode originally?", "dark"),  # originally dark mode
        ("When did I start using Arc browser?", "14"),  # 14 days ago
        ("When did I renew my passport?", "14"),  # 14 days ago
        ("When was the Japan trip originally planned?", "october"),  # originally October, postponed to March
        ("When was the Japan trip postponed to?", "march"),  # postponed to March
        ("What happened with my gym membership?", "cancel"),  # cancelled Lifetime
        ("When did I pay off my student loans?", "14"),  # 14 days ago
        ("What was my original work note app?", "notion"),  # Notion -> Obsidian -> split
        ("When did my dog's paw heal?", "3"),  # 3 days ago
        ("How many times has my parking spot changed?", "3"),  # G-12 -> G-28 -> H-09
        ("What was my project before Revenue Forecasting?", "churn"),  # Churn dashboard
        ("When did I switch from Zoom to...", ""),  # still prefer Zoom
        ("Am I still using Zoom?", "zoom"),  # yes, still prefer Zoom
        ("When did I start meal prepping?", "14"),  # ~14 days ago
        ("What was my original team before Platform?", ""),  # just "data analyst" initially
        ("When did I get my 34-inch monitor?", "60"),  # ~60 days ago
        ("When was Dr. Martinez my dentist?", "60"),  # mentioned 60 days ago
        ("What happened to my dog 2 weeks ago?", "paw"),  # hurt paw
        ("When did I stop using Jira?", "14"),  # ~14 days ago
        ("Who was my manager when I first started?", "karen"),  # Karen Walsh
        ("When did I switch from Python to also using Rust?", "60"),  # 60 days ago
        ("When did I first mention my dad?", "60"),  # 60 days ago
        ("What month was my Japan trip originally?", "october"),
        ("When did I change my phone number?", "14"),  # 14 days ago
        ("When did I start using Notion again for personal?", "3"),  # 3 days ago
        ("How long have I had my YouTube channel?", "7"),  # ~7 days
        ("When did I first mention shellfish allergy?", "90"),  # 90 days ago
        ("When did I start doing meal prep?", "14"),  # 14 days ago
        ("What day did I start working from home on Fridays?", "60"),  # ~60 days ago
        ("What is my current work arrangement on Fridays?", "wfh"),  # WFH Fridays
        ("When did I stop taking the MetroRail?", "14"),  # 14 days ago
        ("When did Priya become Chief?", "7"),  # 7 days ago
        ("When did Biscuit turn 3?", "90"),  # mentioned 90 days ago when she was 3
        ("When did I get my standing desk?", "14"),  # arrived 14 days ago
        ("What was my first video topic?", "kafka"),  # Kafka + Python pipeline
        ("When did my standing desk arrive?", "14"),
        ("When did Rachel move to New York?", ""),  # Rachel doesn't exist - should say doesn't know
        ("How has my team changed over time?", "raj"),  # Raj left, Lisa joined
        ("When did Lisa join my team?", "14"),  # 14 days ago
        ("When did I stop using Chrome?", "14"),  # switched to Arc 14 days ago
        ("What was my original nickname preference?", "jordan"),  # originally Jordan, now JD
        ("When did I start preferring to be called JD?", "7"),  # 7 days ago
    ]
    for q, v in temporal_qs:
        interactions.append({"cat": "temporal", "msg": q, "expect": "temporal reasoning", "validate": v})

    # Phase 3: Knowledge Update — live corrections (151-230)
    update_msgs = [
        ("Actually, I moved to Denver last week. My new address is 1890 Birch St, Denver CO 80202.", "denver"),
        ("My salary went up to $140,000 with the team lead role.", "140"),
        ("Priya transferred to Denver General Hospital. Same role.", "denver general"),
        ("Biscuit is now 4 years old, had her birthday last month.", "4"),
        ("I sold my Honda Civic. I'm just biking everywhere now.", "bik"),
        ("I switched back to dark mode. Light mode was hurting my eyes.", "dark"),
        ("My new emergency contact is my dad, Robert Mitchell, at 713-555-0042.", "robert"),
        ("I no longer prefer Zoom. We switched to Google Meet at work.", "google meet"),
        ("I finished the AWS certification! I'm now an AWS Solutions Architect Associate.", "aws"),
        ("My YouTube channel hit 500 subscribers this week!", "500"),
        ("Noodle is actually a boy cat, not a girl. I keep forgetting to correct that.", "boy"),
        ("I changed dentists. I'm now seeing Dr. Park at Denver Dental Associates.", "park"),
        ("My Japan trip is now confirmed for March 15-29, 2027.", "march 15"),
        ("I started using Figma for design mockups at work.", "figma"),
        ("I stopped going to book club. It was too much with the team lead role.", "stop"),
        ("My new bike is a Specialized Roubaix, not the Trek anymore. The Trek got stolen.", "specialized"),
        ("I changed my work hours to 7:30 AM - 4:00 PM Mountain Time.", "7:30"),
        ("My half marathon time was 1:52! I finished the Austin Half.", "1:52"),
        ("I got a second cat named Mochi. She and Noodle are siblings from the same litter.", "mochi"),
        ("My parking spot in the Denver office is C-07.", "c-07"),
    ]
    update_recall = [
        ("Where do I live now?", "denver"), ("What is my current salary?", "140"),
        ("Where does Priya work now?", "denver general"), ("How old is Biscuit?", "4"),
        ("What car do I drive?", "bike"), ("What mode do I prefer now?", "dark"),
        ("Who is my emergency contact?", "robert"), ("What meeting app do I use?", "google meet"),
        ("Do I have my AWS cert?", "aws"), ("How many YouTube subscribers?", "500"),
        ("Is Noodle male or female?", "boy"), ("Who is my dentist?", "park"),
        ("When is my Japan trip?", "march 15"), ("What new tool am I using?", "figma"),
        ("Am I still in book club?", "no"), ("What bike do I ride now?", "specialized"),
        ("What are my work hours?", "7:30"), ("What was my half marathon time?", "1:52"),
        ("How many cats do I have?", "mochi"), ("What is my parking spot?", "c-07"),
    ]
    for msg, v in update_msgs:
        interactions.append({"cat": "knowledge_update", "msg": msg, "expect": "acknowledge update", "validate": v})
    for q, v in update_recall:
        interactions.append({"cat": "update_recall", "msg": q, "expect": "recall updated fact", "validate": v})

    # Phase 4: Search & History (231-310)
    search_qs = [
        ("What have we discussed about my pets?", "biscuit"),
        ("Search for conversations about my job", "dell"),
        ("Find discussions about my health", "back"),
        ("What did I tell you about my commute?", "metrorail"),
        ("Look up my family details", "priya"),
        ("What were we discussing 2 months ago?", "rust"),
        ("Search for my project history", "churn"),
        ("Find anything about my book club", "hail mary"),
        ("What did I say about allergies?", "shellfish"),
        ("Search for messages about my car", "civic"),
        ("What have we talked about regarding travel?", "japan"),
        ("Show me conversations about my workspace", "standing"),
        ("Find discussions about my certifications", "aws"),
        ("What did I mention about my finances?", "401"),
        ("Search for my preferences about food and drinks", "coffee"),
        ("Look up my exercise and fitness habits", "bik"),
        ("What did I say about my managers?", "karen"),
        ("Find messages about my YouTube channel", "data with jordan"),
        ("Search for anything about my address or location", "austin"),
        ("What did I say about my teammates?", "raj"),
        ("Look up my tech preferences", "neovim"),
        ("Find messages about my home life", "noodle"),
        ("What did I discuss about my work schedule?", "8:30"),
        ("Search for my education and learning interests", "rust"),
        ("What did I say about my dental or medical care?", "martinez"),
        ("Find anything about my weekend activities", "meal"),
        ("Look up my phone and communication details", "8899"),
        ("What have we discussed about my apartment?", "elm creek"),
        ("Search for my project tracking tool changes", "jira"),
        ("What did I mention about my savings and retirement?", "401"),
        ("Find discussions about my parents", "robert"),
        ("What did we discuss about my biking?", "trek"),
        ("Search for mentions of my hobbies", "sci-fi"),
        ("Look up my monitor and desk setup", "ultrawide"),
        ("What did I tell you about my debt?", "student"),
        ("Find messages about Priya's career", "chief"),
        ("Search for my music tastes", "indie"),
        ("What did I say about team changes?", "raj"),
        ("Look up my passport situation", "renew"),
        ("Find my current direct reports", "aisha"),
    ]
    for q, v in search_qs:
        interactions.append({"cat": "search_history", "msg": q, "expect": "use message_search", "validate": v})

    # Phase 5: Preference questions (311-370)
    preference_qs = [
        ("What is my drink preference?", "coffee"),  # oat milk lattes now
        ("What mode do I prefer for my editor?", "dark"),  # switched back to dark
        ("What meeting platform do I prefer?", "google meet"),  # switched from Zoom
        ("What project tracker do I prefer?", "linear"),  # switched from Jira
        ("What note app do I prefer for work?", "obsidian"),
        ("What note app do I prefer for personal?", "notion"),
        ("What browser do I prefer?", "arc"),
        ("What music genre do I prefer?", "indie"),
        ("What streaming service do I prefer?", "spotify"),
        ("What reading genre do I prefer?", "sci-fi"),
        ("What editor do I prefer for coding?", "neovim"),
        ("Do I prefer biking or driving to work?", "bik"),
        ("What kind of coffee do I like?", "oat milk"),
        ("Do I prefer working from home or office?", "friday"),  # WFH Fridays
        ("What is my lunch preference?", "torchy"),  # Torchy's Tacos
        ("What video platform do I use for content?", "youtube"),
        ("Do I prefer cats or dogs?", ""),  # has both!
        ("What genre of fiction do I like?", "sci-fi"),
        ("What type of car do I prefer?", "bike"),  # sold car, bikes now
        ("What's my preferred way to be addressed?", "jd"),
        ("What author do I prefer?", "tchaikovsky"),
        ("What book did I enjoy recently?", "hail mary"),
        ("Do I prefer team lead or individual contributor?", "team lead"),
        ("What cloud provider am I certifying in?", "aws"),
        ("What kind of bike do I prefer for commuting?", "specialized"),  # updated from Trek
        ("Do I prefer morning or evening exercise?", "6am"),  # originally gym at 6am
        ("What desktop theme do I prefer?", "dark"),  # back to dark
        ("What communication tool does my team use?", "slack"),
        ("What is my preferred work-from-home day?", "friday"),
    ]
    for q, v in preference_qs:
        interactions.append({"cat": "preference", "msg": q, "expect": "recall preference", "validate": v})

    # Phase 6: Complex syntheses (371-440)
    complex_qs = [
        ("Give me a full profile summary of everything you know about me", "jd"),
        ("What are the most important changes in my life recently?", "denver"),
        ("Summarize my career progression at Dell", "senior"),
        ("What does my typical work week look like?", "7:30"),
        ("Tell me about my family situation in detail", "priya"),
        ("What are all my pets' details?", "biscuit"),
        ("Create a contact card for me with all my info", "512"),
        ("What are my current financial details?", "401"),
        ("Summarize my tech stack and tool preferences", "python"),
        ("What does my morning routine look like based on what I've told you?", "7:30"),
        ("If someone needed to reach me in an emergency, what should they do?", "robert"),
        ("What are 5 key facts about my work life?", "dell"),
        ("What are 5 key facts about my personal life?", "priya"),
        ("Compare my work life 90 days ago vs now", "team lead"),
        ("What have been the biggest turning points in my time using this assistant?", "promotion"),
        ("What does my social circle look like?", "tyler"),
        ("What are my upcoming plans and events?", "japan"),
        ("What has changed about my living situation?", "denver"),
        ("Summarize everything about my pets", "biscuit"),
        ("What are my hobbies and interests?", "sci-fi"),
        ("What does my team at work look like?", "aisha"),
        ("Tell me about my education and learning path", "rust"),
        ("What are my health-related details?", "shellfish"),
        ("Compare my preferences from day 1 vs today", "dark"),
        ("What transportation changes have I made?", "bik"),
        ("I need a summary for my new doctor — what health info do you have?", "shellfish"),
        ("What are all the projects I've worked on?", "pipeline"),
        ("Give me the timeline of my career at Dell", "data analyst"),
        ("What do you know about my YouTube channel?", "data with jordan"),
        ("If I had to describe myself using only what you know, what would you say?", "jd"),
    ]
    for q, v in complex_qs:
        interactions.append({"cat": "complex", "msg": q, "expect": "synthesize", "validate": v})

    # Phase 7: Idempotency / Repeat questions (441-460)
    repeat_qs = [
        ("What is my name?", "jordan"), ("Where do I live?", "denver"),
        ("What is my wife's name?", "priya"), ("What is my job?", "dell"),
        ("Do I have pets?", "biscuit"), ("What is my current project?", "pipeline"),
    ]
    for _ in range(3):
        for q, v in repeat_qs:
            interactions.append({"cat": "idempotent", "msg": q, "expect": "consistent answer", "validate": v})
    # remove extras
    interactions = interactions[:460]

    # Phase 8: Edge / Stress (461-500)
    edge_qs = [
        ("Can you search for something that doesn't exist — my secret underground bunker?", ""),
        ("What is something I've never told you?", ""),
        ("How many total facts do you know about me?", ""),
        ("What is 2+2? (Just checking you're still working)", "4"),
        ("If I asked you to forget everything, would you?", ""),
        ("What is the most private thing I've shared with you?", "vpn"),
        ("Can you list all my preferences in order of confidence?", ""),
        ("What's the oldest memory you have of me?", "jordan"),
        ("What's the newest thing I've told you?", "denver"),
        ("Pretend you're me for a moment. Introduce yourself.", "jd"),
        ("What is my blood type?", ""),  # never mentioned
        ("Do I have any siblings?", ""),  # never mentioned
        ("What is my mother's name?", ""),  # never mentioned
        ("What time is it right now?", ""),
        ("What is my shoe size?", ""),  # never mentioned
        ("Summarize our relationship in one sentence.", ""),
        ("What are my top 3 priorities based on what you know?", ""),
        ("If I were to move again, what would I need to update?", "address"),
        ("What's the funniest thing I've told you?", ""),
        ("Goodbye for now. I'll be back later.", ""),
        ("I'm back! What do you remember about me?", "jordan"),
        ("What did we talk about last time?", "denver"),
        ("Am I a morning person or night owl based on what you know?", "7:30"),
        ("What language should I learn next?", ""),
        ("What is my license plate again?", "4521"),
        ("Between Python and Rust, which am I better at?", "python"),
        ("What is my dentist's name?", "park"),
        ("What is the name of my new kitten?", "mochi"),
        ("How many animals do I have?", "3"),  # dog + 2 cats
        ("What is my Denver address?", "1890 birch"),
        ("When did I move to Denver?", ""),
        ("What is Priya's new hospital?", "denver general"),
        ("What happened to my Honda Civic?", "sold"),
        ("What happened to my Trek bike?", "stolen"),
        ("What happened to my Lifetime Fitness membership?", "cancel"),
        ("How long has it been since my last meeting with you?", ""),
        ("What was the very first thing I ever told you?", "jordan mitchell"),
        ("What is my preferred nickname with friends?", "jd"),
        ("What cloud certification do I have?", "aws"),
        ("Do I still owe any student loans?", "paid"),
    ]
    for q, v in edge_qs:
        interactions.append({"cat": "stress", "msg": q, "expect": "handles gracefully", "validate": v})

    # Phase 9: More fact recall — deep history (501-575)
    deep_recall = [
        ("What building is my office in?", "building 4"),
        ("What floor is my desk on?", "floor 2"),
        ("What is Priya's specialty?", "emergency"),
        ("What hospital did Priya originally work at?", "austin general"),
        ("What is my Dell email address?", "jordan.mitchell@dell"),
        ("Where is my dad's city?", "houston"),
        ("What is my dad's name again?", "robert"),
        ("What kind of dog is Biscuit?", "golden"),
        ("What is my original parking spot at Dell garage?", "g-12"),
        ("What was my very first project at Dell?", "churn"),
        ("What model Honda did I drive?", "civic"),
        ("What year was my Honda?", "2021"),
        ("What is my wife's birthday month?", "november"),
        ("What is my birthday month?", "june"),
        ("What is the closest MetroRail stop to my office?", "kramer"),
        ("What is my VPN code format?", "dm"),
        ("What author do I like for sci-fi?", "tchaikovsky"),
        ("What was my first book club book?", "hail mary"),
        ("What is my preferred note-taking structure?", "obsidian"),
        ("What was my original 401k contribution percentage?", "8"),
        ("What is the Dell 401k match percentage?", "5"),
        ("What color is Biscuit?", "golden"),
        ("What is my half marathon distance?", "half marathon"),
        ("What city is my half marathon in?", "austin"),
        ("Where is Torchy's Tacos located?", "lamar"),
        ("What is my work email domain?", "dell"),
        ("What timezone am I in now?", "mountain"),
        ("What state did I move to?", "colorado"),
        ("What was my original state?", "texas"),
        ("What streaming service do I use for music?", "spotify"),
        ("What kind of music playlists do I make?", "indie"),
        ("What is my book club meeting schedule?", "wednesday"),
        ("What video did I make about Kafka?", "kafka"),
        ("What is my new bike brand after Specialized?", "specialized"),
        ("What happened to my first Trek bike?", "stolen"),
        ("What is my YouTube channel about?", "data"),
        ("What is my wife's new hospital?", "denver general"),
        ("What is my new dentist's name?", "park"),
        ("What dental office do I go to?", "denver dental"),
        ("What is Mochi's relationship to Noodle?", "sibling"),
        ("What gender is Noodle?", "boy"),
        ("What is my new commute distance?", "6"),
        ("What type of monitor did I get from Dell?", "ultrawide"),
        ("What size is my work monitor?", "34"),
        ("What is my standing desk brand?", "uplift"),
        ("What dental issue did I have with my old dentist?", "martinez"),
        ("What does Tyler do for work?", "samsung"),
        ("What city does Tyler work in?", "austin"),
        ("What is my phone model?", "iphone 16"),
        ("What was my job before Senior Data Analyst?", "data analyst"),
        ("What was my salary before the team lead raise?", "125"),
        ("What is the name of my new teammate from Seattle?", "lisa chen"),
        ("What team did Raj move to?", "security"),
        ("What is my new direct report's name — starts with M?", "marco"),
        ("What is Aisha's last name?", "patel"),
        ("What is Dev's last name?", "sharma"),
        ("What is my apartment number in Denver?", "3b"),
        ("What street is my Denver apartment on?", "birch"),
        ("What is my Denver zip code?", "80202"),
        ("What is my original Austin address?", "elm creek"),
        ("What was my Austin zip code?", "78745"),
        ("What kind of milk do I put in my coffee?", "oat"),
        ("How did I find out about my shellfish allergy?", "company dinner"),
        ("What is my car situation now?", "bike"),
        ("What was my original meeting platform preference?", "zoom"),
        ("What cloud exam am I taking?", "solutions architect"),
        ("What is my team size now?", "4"),
        ("What month was the Japan trip originally planned?", "october"),
        ("When is the rescheduled Japan trip?", "march"),
        ("How long is the Japan trip?", "2 weeks"),
        ("What was my first video about on YouTube?", "kafka"),
        ("Am I still doing the half marathon in December?", "1:52"),
        ("What was my half marathon finish time?", "1:52"),
        ("What is my current work start time?", "7:30"),
        ("What is my current work end time?", "4:00"),
    ]
    for q, v in deep_recall:
        interactions.append({"cat": "fact_recall", "msg": q, "expect": "recall fact", "validate": v})

    # Phase 10: More temporal + search (576-650)
    more_temporal = [
        ("What was my commute method 3 months ago?", "metrorail"),
        ("What was my project 2 months ago?", "revenue"),
        ("When did I start using Obsidian for notes?", "30"),
        ("When did my wife get promoted?", "7"),
        ("What was my preferred theme 2 months ago?", "light"),
        ("What was my preferred theme on day 1?", "dark"),
        ("What was my original phone number?", "555-0123"),
        ("What happened to my phone number 2 weeks ago?", "8899"),
        ("When did I stop going to Lifetime Fitness?", "14"),
        ("What happened to my gym membership?", "cancel"),
        ("When did I first mention my 401k?", "60"),
        ("When did I increase my 401k?", "3"),
        ("What did I use for notes before Obsidian?", "notion"),
        ("When did I switch from Zoom to Google Meet?", "just"),  # recent update
        ("When did Biscuit's paw injury happen?", "14"),
        ("When did Biscuit fully recover?", "3"),
        ("How long was Biscuit injured?", "2 weeks"),
        ("When did I get my 34-inch ultrawide monitor?", "60"),
        ("When did I tell you about my new standing desk?", "30"),
        ("When did my standing desk arrive?", "14"),
        ("What was my team name before Pipeline?", "analytics platform"),
        ("When did Karen retire?", "7"),
        ("When did I start as team lead?", "3"),
        ("When did I get my promotion to Senior?", "30"),
        ("How many parking spots have I had total?", "4"),  # G-12, G-28, H-09, C-07
        ("When did I first mention I'm a team lead?", "3"),
        ("When did I first talk about Rust?", "60"),
        ("What happened to my parking spot over time?", "g-12"),
        ("When did I change from tea back to coffee?", "3"),
        ("When did I change my editor from VS Code to Neovim?", "7"),
        ("What changes happened in the last 14 days?", "denver"),
        ("What changes happened 30 days ago?", "promotion"),
        ("What was different about my life 90 days ago vs today?", "data analyst"),
        ("When did I first start using this assistant?", "90"),
        ("What was my original role when we first met?", "data analyst"),
    ]
    for q, v in more_temporal:
        interactions.append({"cat": "temporal", "msg": q, "expect": "temporal reasoning", "validate": v})

    # Phase 11: More complex syntheses (651-720)
    more_complex = [
        ("Create a table of all address changes I've had", "austin"),
        ("What is the full history of my parking spots?", "g-12"),
        ("List all my pets with their details", "biscuit"),
        ("What is the complete timeline of my career changes?", "data analyst"),
        ("What are all my current and past commutes?", "metrorail"),
        ("Show me how my tech stack has evolved", "python"),
        ("What are all the places I've mentioned living or visiting?", "austin"),
        ("Summarize all my health information", "shellfish"),
        ("What is my complete family overview?", "priya"),
        ("Give me a timeline of my project assignments", "churn"),
        ("What are all my financial details?", "401"),
        ("What are all my hobbies in order of recency?", "youtube"),
        ("What is the full history of my drink preferences?", "dark roast"),
        ("What are all the animals in my household?", "biscuit"),
        ("List every manager I've had and when they changed", "karen"),
        ("Create an org chart of my team", "aisha"),
        ("What are all the certifications I've mentioned?", "aws"),
        ("What were all the reasons for my major life changes?", "promotion"),
        ("Give me a comprehensive travel summary", "japan"),
        ("What social media or content platforms do I use?", "youtube"),
        ("What does my digital life look like — apps, tools, services?", "spotify"),
        ("What's the difference between my life 90 days ago and now?", "denver"),
        ("What are 3 things that stayed the same about me?", "dell"),
        ("What are 3 things that changed about me in the last month?", "denver"),
        ("If you had to write my biography in 3 paragraphs, what would it say?", "jordan"),
        ("What is the smartest financial decision I've made?", "student"),
        ("What is the most surprising thing I've told you?", "biscuit"),
        ("What do you think I'll be doing 6 months from now?", ""),
        ("What questions should I ask my new doctor?", "shellfish"),
        ("What would be in my emergency grab-and-go bag?", "passport"),
        ("What are my current and past phone numbers?", "8899"),
        ("What does my ideal weekend look like based on what you know?", ""),
        ("What are my top skills and what should I learn next?", "rust"),
        ("Who are all the people I've mentioned by name?", "priya"),
        ("What do you know about my neighborhood?", "birch"),
        ("What are all the vehicles I've owned or used?", "civic"),
        ("What would my dating profile look like if I were single?", "sci-fi"),
        ("What is my complete coffee/tea history?", "dark roast"),
        ("What are all the tools I've used or currently use?", "python"),
        ("Give me my complete relocation summary", "austin"),
        ("Am I more of a morning person or evening person?", "7:30"),
        ("What is my overall health profile?", "shellfish"),
        ("What is my current city and state?", "denver"),
        ("What major milestones have I achieved?", "paid"),
        ("What does my morning routine look like now?", "7:30"),
        ("What does my evening routine look like?", ""),
        ("What is my current project at work?", "pipeline"),
        ("What is my most recent career update?", "team lead"),
        ("Who is the most important person in my life based on what I've told you?", "priya"),
        ("What are my financial goals based on what I've shared?", "401"),
        ("What is my current fitness routine?", "bik"),
        ("What are my top 3 favorite restaurants or food spots?", "torchy"),
        ("What would I pack for my Japan trip?", "passport"),
        ("What does my home setup look like?", "standing"),
        ("What are all the pets I've had?", "biscuit"),
        ("What is my current address and how has it changed?", "1890"),
        ("What is Priya's current role and hospital?", "denver general"),
        ("What does my Dell org look like now?", "tom"),
        ("What is my coffee order at a cafe?", "oat milk"),
        ("What are my transportation options?", "bike"),
        ("What happened on each of my parking spot changes?", "g-12"),
        ("What is the complete history of my editor/IDE usage?", "vs code"),
        ("What is my current meeting platform preference and history?", "google meet"),
        ("What is the complete history of my project tracking tool?", "jira"),
    ]
    for q, v in more_complex:
        interactions.append({"cat": "complex", "msg": q, "expect": "synthesize", "validate": v})

    assert len(interactions) >= 500, f"Only have {len(interactions)}, need 500"
    return interactions[:500]


INTERACTIONS = _build_interactions()
assert len(INTERACTIONS) == 500, f"Expected 500, got {len(INTERACTIONS)}"


async def run_single_turn(ws: Any, idx: int, interaction: dict[str, str]) -> TurnResult:
    result = TurnResult(
        interaction_id=idx,
        category=interaction["cat"],
        input_message=interaction["msg"],
        expected_behavior=interaction["expect"],
    )
    msg = {"type": "user_message", "content": interaction["msg"], "user_id": USER_ID}
    start = time.monotonic()
    try:
        await ws.send(json.dumps(msg))
        text_parts: list[str] = []
        tool_names: list[str] = []
        event_types: list[str] = []
        async for raw in ws:
            if isinstance(raw, bytes):
                raw = raw.decode()
            data = json.loads(raw)
            evt_type = data.get("type", "unknown")
            event_types.append(evt_type)
            if evt_type == "text_delta":
                text_parts.append(data.get("content", ""))
            elif evt_type == "tool_input_start":
                tool_names.append(data.get("tool", "unknown"))
            elif evt_type == "tool_result":
                tool_names.append(data.get("tool", ""))
            elif evt_type == "done":
                result.got_done = True
                break
            elif evt_type == "error":
                result.got_error = True
                result.validation_detail = data.get("message", "unknown error")
                break
            elif evt_type == "interrupt":
                approve_msg = {"type": "approve", "call_id": data.get("call_id", "")}
                await ws.send(json.dumps(approve_msg))
        result.response_text = "".join(text_parts)
        result.got_text = len(result.response_text) > 0
        result.got_tool_call = len(tool_names) > 0
        result.tool_names = tool_names
        result.event_types_received = event_types
    except Exception as e:
        result.got_error = True
        result.validation_detail = str(e)
    result.elapsed_ms = (time.monotonic() - start) * 1000
    validate_token = interaction["validate"].lower()
    if validate_token:
        response_lower = result.response_text.lower()
        if validate_token in response_lower:
            result.validation_passed = True
            result.validation_detail = f"Found '{validate_token}'"
        else:
            result.validation_passed = False
            result.validation_detail = f"Missing '{validate_token}' ({len(result.response_text)} chars)"
    else:
        result.validation_passed = result.got_done and not result.got_error and result.got_text
        result.validation_detail = "No token — OK if no error"
    return result


async def run_all() -> list[TurnResult]:
    results: list[TurnResult] = []
    start_time = time.monotonic()

    print(f"Seeding {len(SEED_DATA)} historical messages across 30-90 days ago...")
    seed_count = seed_messages()
    print(f"Seeded {seed_count} messages.\n")

    print(f"Connecting to {WS_URL} ...")
    async with websockets.connect(WS_URL, ping_interval=30, ping_timeout=60, close_timeout=30) as ws:
        print(f"Connected! Running {len(INTERACTIONS)} interactions...\n")

        for idx, interaction in enumerate(INTERACTIONS, 1):
            result = await asyncio.wait_for(
                run_single_turn(ws, idx, interaction),
                timeout=TIMEOUT_PER_TURN,
            )
            results.append(result)

            status = "OK" if result.validation_passed else "FAIL"
            tool_str = f" [{', '.join(result.tool_names[:3])}]" if result.tool_names else ""
            if idx % 25 == 0 or not result.validation_passed or result.got_error:
                elapsed_total = (time.monotonic() - start_time) / 60
                print(
                    f"[{idx:3d}/500] {status} | {result.category:<16s} | "
                    f"{result.elapsed_ms:6.0f}ms | "
                    f"text={result.got_text} done={result.got_done} tools={len(result.tool_names)}"
                    f"{tool_str} | {elapsed_total:.1f}m elapsed"
                    + (f" | {result.validation_detail[:50]}" if not result.validation_passed else "")
                )

    return results


def print_report(results: list[TurnResult]) -> None:
    total = len(results)
    passed = sum(1 for r in results if r.validation_passed)
    errors = sum(1 for r in results if r.got_error)
    got_done = sum(1 for r in results if r.got_done)
    got_text = sum(1 for r in results if r.got_text)
    used_tools = sum(1 for r in results if r.got_tool_call)
    avg_ms = sum(r.elapsed_ms for r in results) / total if total else 0
    total_time_min = sum(r.elapsed_ms for r in results) / 60000

    print("\n" + "=" * 90)
    print("WS MEMORY INTEGRATION TEST — 500 INTERACTIONS (DATE-SPACED)")
    print("=" * 90)

    print(f"\nOverall: {passed}/{total} passed ({passed/total*100:.1f}%)")
    print(f"Errors:  {errors}/{total}")
    print(f"Got 'done': {got_done}/{total}")
    print(f"Got text:   {got_text}/{total}")
    print(f"Used tools: {used_tools}/{total}")
    print(f"Avg latency: {avg_ms:.0f}ms | Total time: {total_time_min:.1f}min")
    print(f"Pre-seeded messages: {len(SEED_DATA)} across 30-90 days")

    print("\n--- By Category ---")
    categories: dict[str, list[TurnResult]] = {}
    for r in results:
        categories.setdefault(r.category, []).append(r)
    for cat in sorted(categories.keys()):
        cr = categories[cat]
        cp = sum(1 for r in cr if r.validation_passed)
        ct = len(cr)
        ce = sum(1 for r in cr if r.got_error)
        ct2 = sum(1 for r in cr if r.got_tool_call)
        ca = sum(r.elapsed_ms for r in cr) / ct if ct else 0
        print(f"  {cat:<20s}: {cp}/{ct} passed ({cp/ct*100:.0f}%) | err={ce} tools={ct2} avg={ca:.0f}ms")

    print("\n--- Tool Usage Stats ---")
    all_tools: dict[str, int] = {}
    for r in results:
        for t in r.tool_names:
            if t:
                all_tools[t] = all_tools.get(t, 0) + 1
    for tool, count in sorted(all_tools.items(), key=lambda x: -x[1]):
        print(f"  {tool:<30s}: {count}")

    print("\n--- Event Type Distribution ---")
    all_events: dict[str, int] = {}
    for r in results:
        for e in r.event_types_received:
            all_events[e] = all_events.get(e, 0) + 1
    for evt, count in sorted(all_events.items(), key=lambda x: -x[1]):
        print(f"  {evt:<25s}: {count}")

    print("\n--- Failures by Category ---")
    failures = [r for r in results if not r.validation_passed]
    fail_by_cat: dict[str, int] = {}
    for r in failures:
        fail_by_cat[r.category] = fail_by_cat.get(r.category, 0) + 1
    if not failures:
        print("  None!")
    else:
        for cat, count in sorted(fail_by_cat.items(), key=lambda x: -x[1]):
            print(f"  {cat:<20s}: {count} failures")
        print("\n  Sample failures (first 20):")
        for r in failures[:20]:
            print(
                f"  [{r.interaction_id:3d}] {r.category:<16s} | "
                f"{r.input_message[:50]:<50s} | {r.validation_detail[:60]}"
            )

    print("\n--- Summary ---")
    print(f"  Pass rate: {passed/total*100:.1f}%")
    print(f"  WS stability: {'PASS' if errors == 0 else 'FAIL'} ({errors} errors)")
    print(f"  Protocol: {'PASS' if got_done == total else 'PARTIAL'} ({got_done}/{total} done)")
    print(f"  Memory tools: {used_tools}/{total} interactions used tools")
    print(f"  Recall accuracy: {passed}/{total}")
    if passed >= 90:
        print("\n  STATUS: EXCELLENT")
    elif passed >= 75:
        print("\n  STATUS: GOOD")
    elif passed >= 50:
        print("\n  STATUS: NEEDS IMPROVEMENT")
    else:
        print("\n  STATUS: CRITICAL ISSUES")


async def main() -> None:
    results = await run_all()
    print_report(results)

    report_path = "data/benchmarks/results/ws_memory_500_results.json"
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(
            [
                {
                    "id": r.interaction_id,
                    "category": r.category,
                    "input": r.input_message,
                    "expected": r.expected_behavior,
                    "passed": r.validation_passed,
                    "detail": r.validation_detail,
                    "got_done": r.got_done,
                    "got_error": r.got_error,
                    "got_text": r.got_text,
                    "tools": r.tool_names,
                    "elapsed_ms": round(r.elapsed_ms),
                }
                for r in results
            ],
            f,
            indent=2,
        )
    print(f"\nResults saved to {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
