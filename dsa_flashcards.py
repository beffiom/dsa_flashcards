import argparse
import json
import os
import random
import sys
import sqlite3
import time
from prettytable import PrettyTable
from anki_sm_2 import Scheduler, Card, Rating
from datetime import datetime, timezone

def load_deck(deck_name):
    deck_path = os.path.join("json", f"{deck_name}.json")
    if not os.path.isfile(deck_path):
        print (f"Deck {deck_name} not found at path {deck_path}")
        sys.exit(1)
    with open(deck_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def create_db(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_uuid TEXT UNIQUE,
            deck_name TEXT,
            card_name TEXT UNIQUE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scheduling (
            card_id INTEGER PRIMARY KEY,
            interval INTEGER,
            repetitions INTEGER,
            ease REAL,
            due TEXT,
            FOREIGN KEY(card_id) REFERENCES cards(id)
        )
    ''')

    conn.commit()
    conn.close()

def card_exists_in_db(card_uuid, db_path):
    query = "SELECT 1 FROM cards WHERE card_uuid = ? LIMIT 1"
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, (card_uuid,))
            result = cursor.fetchone()
            return result is not None
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return False

def add_new_card_to_db(card_uuid, card_name, deck_name, db_path='./db.sqlite'):
    insert_query = '''
        INSERT INTO cards (card_uuid, card_name, deck_name) VALUES (?, ?, ?)
    '''
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(insert_query, (card_uuid, card_name, deck_name))
            conn.commit()
            return cursor.lastrowid
    except sqlite3.IntegrityError as e:
        print(f"Failed to add card with UUID {card_uuid}: {e}")
        return None
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return None

def update_card_in_db(card_uuid, card, db_path):
    """
    Update the scheduling table for card identified by card_uuid.

    Args:
        card_uuid (str): UUID of the card.
        card (anki_sm_2.Card): The card scheduling info after review (with datetime due).
        db_path (str): Path to SQLite DB.
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        # Get card_id for this card_uuid
        cursor.execute("SELECT id FROM cards WHERE card_uuid = ?", (card_uuid,))
        row = cursor.fetchone()
        if not row:
            print(f"Card with UUID {card_uuid} not found in database when updating scheduling.")
            return
        card_id = row[0]

        # Serialize due date as ISO8601 string (can be None)
        due_iso = card.due.isoformat() if card.due else None

        # Check if scheduling info exists for this card
        cursor.execute("SELECT 1 FROM scheduling WHERE card_id = ?", (card_id,))
        exists = cursor.fetchone()

        if exists:
            cursor.execute('''
                UPDATE scheduling
                SET interval = ?, repetitions = ?, ease = ?, due = ?
                WHERE card_id = ?
            ''', (
                card.interval,
                card.repetitions,
                card.ease,
                due_iso,
                card_id
            ))
        else:
            cursor.execute('''
                INSERT INTO scheduling (card_id, interval, repetitions, ease, due) 
                VALUES (?, ?, ?, ?, ?)
            ''', (
                card_id,
                card.interval,
                card.repetitions,
                card.ease,
                due_iso
            ))
        conn.commit()

def anki_select_card(deck, db_path):
    """
    Select one card due for review based on scheduling; if none, pick random and add to DB.

    Args:
        deck (list of dict): The loaded deck from JSON.
        db_path (str): DB file path.

    Returns:
        (dict, Card): The card dict selected and its scheduling Card instance.
    """
    now = datetime.now(timezone.utc)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        # Find card_id with due date <= now (card due for review)
        cursor.execute('''
            SELECT c.deck_name, c.card_uuid, c.card_name, s.interval, s.repetitions, s.ease, s.due
            FROM cards c
            JOIN scheduling s ON c.id = s.card_id
            WHERE s.due IS NOT NULL AND s.due <= ?
            ORDER BY s.due ASC LIMIT 1
        ''', (now.isoformat(),))
        row = cursor.fetchone()

        if row:
            deck_name, card_uuid, card_name, interval, repetitions, ease, due_str = row
            card_data = next((c for c in deck if c['card_uuid'] == card_uuid), None)
            if card_data is None:
                if deck_name == deck[0]['deck_name']:
                    print(f"Warning: card_uuid {card_uuid} found in DB but missing in JSON deck")
            else:
                due_dt = datetime.fromisoformat(due_str) if due_str else now
                scheduler_card = Card()
                scheduler_card.interval = interval
                scheduler_card.repetitions = repetitions
                scheduler_card.ease = ease
                scheduler_card.due = due_dt
                return card_data, scheduler_card

        # No due cards found, pick a random card from deck
        card_data = random.choice(deck)
        card_uuid = card_data['card_uuid']

        cursor.execute("SELECT id FROM cards WHERE card_uuid = ?", (card_uuid,))
        card_row = cursor.fetchone()
        if not card_row:
            cursor.execute(
                "INSERT INTO cards (card_uuid, deck_name, card_name) VALUES (?, ?, ?)",
                (card_uuid, card_data['deck_name'], card_data['card_name'])
            )
            conn.commit()
            card_id = cursor.lastrowid
        else:
            card_id = card_row[0]

        # Insert initial scheduling data if not exists
        cursor.execute("SELECT 1 FROM scheduling WHERE card_id = ?", (card_id,))
        if not cursor.fetchone():

            initial_sched = Card()
            initial_sched.interval = 0
            initial_sched.repetitions = 0
            initial_sched.ease = 2.5
            initial_sched.due = now
            cursor.execute(
                "INSERT INTO scheduling (card_id, interval, repetitions, ease, due) VALUES (?, ?, ?, ?, ?)",
                (card_id, initial_sched.interval, initial_sched.repetitions, initial_sched.ease, initial_sched.due.isoformat())
            )
            conn.commit()
            return card_data, initial_sched

        # If scheduling unexpectedly exists but no due cards found, pick the first scheduled card
        cursor.execute('''
            SELECT c.card_uuid, c.card_name, s.interval, s.repetitions, s.ease, s.due
            FROM cards c
            JOIN scheduling s ON c.id = s.card_id
            ORDER BY s.due ASC LIMIT 1
        ''')
        row = cursor.fetchone()
        if row:
            card_uuid, card_name, interval, repetitions, ease, due_str = row
            card_data = next((c for c in deck if c['card_uuid'] == card_uuid), None)
            if card_data:
                due_dt = datetime.fromisoformat(due_str) if due_str else now
                scheduler_card = Card()
                scheduler_card.interval = interval
                scheduler_card.repetitions = repetitions
                scheduler_card.ease = ease
                scheduler_card.due = due_dt
                return card_data, scheduler_card

        # Fallback: just return the random card with initial scheduling
        initial_sched = Card(interval=0, repetitions=0, ease=2.5, due=now)
        return card_data, initial_sched

def display_card(card_data, scheduler_card, db_path):
    """
    Show front (card_name), wait for user, then show back (remaining fields) in PrettyTable.
    Prompt user for difficulty rating and update scheduling in DB.

    Args:
        card_data (dict): The card dict loaded from JSON.
        scheduler_card (anki_sm_2.Card): Current scheduling state.
        db_path (str): Path to database.
    """
    input(f"\n\033[1m{card_data['card_name']}\033[0m\n{card_data['description']}\n\nPress Enter to show the back...")

    print("\n" + "-"*50)
    for key, value in card_data.items():
        if key in ('card_uuid', 'description'):
            continue
        print(f"\n\033[1m{key.replace('_', ' ').capitalize()}:\033[0m")  # bold header
        print(value.strip() if value else "")
    print("-"*50 + "\n")

    print("\nEvaluate your recall difficulty:")
    print("1: Again (failed to recall)")
    print("2: Hard")
    print("3: Good")
    print("4: Easy")
    while True:
        rating_input = input("Enter 1,2,3 or 4: ").strip()
        if rating_input in ("1", "2", "3", "4"):
            break
        print("Invalid input, please enter 1, 2, 3, or 4.")

    rating_map = {
        "1": Rating.Again,
        "2": Rating.Hard,
        "3": Rating.Good,
        "4": Rating.Easy
    }
    rating = rating_map[rating_input]

    new_sched_card = evaluate_difficulty(scheduler_card, rating)
    update_card_in_db(card_data['card_uuid'], new_sched_card, db_path)

    print("Scheduling updated.")

def evaluate_difficulty(card, rating):
    """
    Given a card and user's recall rating, update card scheduling using anki_sm_2 Scheduler.

    Args:
        card (anki_sm_2.Card): the current state of the card.
        rating (anki_sm_2.Rating): user rating.

    Returns:
        anki_sm_2.Card: updated scheduling info.
    """
    scheduler = Scheduler()
    updated_card, _ = scheduler.review_card(card, rating)
    return updated_card

def random_select_card(deck, db_path):
    card_data = random.choice(deck)
    card_uuid = card_data["card_uuid"]

    now = datetime.now(timezone.utc)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM cards WHERE card_uuid = ?", (card_uuid,))
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                "INSERT INTO cards (card_uuid, deck_name, card_name) VALUES (?, ?, ?)",
                (card_uuid, card_data['deck_name'], card_data['card_name'])
            )
            conn.commit()
            card_id = cursor.lastrowid
        else:
            card_id = row[0]

        cursor.execute("SELECT interval, repetitions, ease, due FROM scheduling WHERE card_id = ?", (card_id,))
        sched_row = cursor.fetchone()

        if sched_row:
            interval, repetitions, ease, due_str = sched_row
            due_dt = datetime.fromisoformat(due_str) if due_str else now
            scheduler_card = Card()
            scheduler_card.interval = interval
            scheduler_card.repetitions = repetitions
            scheduler_card.ease = ease
            scheduler_card.due = due_dt
        else:
            scheduler_card = Card()
            scheduler_card.interval = 0
            scheduler_card.repetitions = 0
            scheduler_card.ease = 2.5
            scheduler_card.due = now
            cursor.execute(
                "INSERT INTO scheduling (card_id, interval, repetitions, ease, due) VALUES (?, ?, ?, ?, ?)",
                (card_id, scheduler_card.interval, scheduler_card.repetitions, scheduler_card.ease, scheduler_card.due.isoformat())
            )
            conn.commit()

    return card_data, scheduler_card

def get_card_by_name(deck, card_name, db_path):
    card_data = next(
        (card for card in deck if card['card_name'].lower() == card_name.lower()),
        None
    )
    if not card_data:
        return None, None

    card_uuid = card_data['card_uuid']
    now = datetime.now(timezone.utc)

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM cards WHERE card_uuid = ?", (card_uuid,))
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                "INSERT INTO cards (card_uuid, deck_name, card_name) VALUES (?, ?, ?)",
                (card_uuid, card_data['deck_name'], card_data['card_name'])
            )
            conn.commit()
            card_id = cursor.lastrowid
        else:
            card_id = row[0]

        cursor.execute("SELECT interval, repetitions, ease, due FROM scheduling WHERE card_id = ?", (card_id,))
        sched_row = cursor.fetchone()

        if sched_row:
            interval, repetitions, ease, due_str = sched_row
            due_dt = datetime.fromisoformat(due_str) if due_str else now
            scheduler_card = Card()
            scheduler_card.interval = interval
            scheduler_card.repetitions = repetitions
            scheduler_card.ease = ease
            scheduler_card.due = due_dt
        else:
            scheduler_card = Card()
            scheduler_card.interval = interval
            scheduler_card.repetitions = repetitions
            scheduler_card.ease = ease
            scheduler_card.due = due_dt
            cursor.execute(
                "INSERT INTO scheduling (card_id, interval, repetitions, ease, due) VALUES (?, ?, ?, ?, ?)",
                (card_id, scheduler_card.interval, scheduler_card.repetitions, scheduler_card.ease, scheduler_card.due.isoformat())
            )
            conn.commit()

    return card_data, scheduler_card

def list_cards(deck):
    """
    Print all card names in the deck using PrettyTable format.
    """
    table = PrettyTable()
    table.field_names = ["Card Name"]

    for card in deck:
        card_name = card.get('card_name', '')
        table.add_row([card_name])

    print(table)

def search_card(deck, card_name):
    """
    Print a single card from the deck where card['card_name'] matches card_name (case-insensitive),
    excluding a fixed set of fields, with bolded field names in block format.
    """
    if not deck:
        print("Deck is empty.")
        return

    exclude_fields = set()
    deck_name = deck[0].get('deck_name')
    if deck_name == "neetcode150":
        exclude_fields = {"card_uuid"}

    found = False
    for card in deck:
        if card.get('card_name', '').lower() == card_name.lower():
            found = True
            fields_to_show = [k for k in card.keys() if k not in exclude_fields]
            for field in fields_to_show:
                value = card[field]
                print(f"\033[1m{field.replace('_',' ').capitalize()}:\033[0m")
                print(value if value is not None else "")
                print()  # blank line between fields
            print('-'*40)  # separator between cards (only one in this case)
            break
    if not found:
        print(f"No card found with name '{card_name}'.")

def list_cards_in_db(deck, db_path):
    uuid_to_name = {card['card_uuid']: card['card_name'] for card in deck}

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        cursor.execute('''
            SELECT c.card_uuid, c.card_name, s.interval, s.repetitions, s.ease, s.due
            FROM cards c
            LEFT JOIN scheduling s ON c.id = s.card_id
            WHERE c.deck_name = ?
            ORDER BY c.card_name COLLATE NOCASE ASC
        ''', (deck[0]['deck_name'],))

        table = PrettyTable()
        table.field_names = ["Card Name", "Interval", "Repetitions", "Ease", "Due"]

        for row in cursor.fetchall():
            card_uuid, card_name, interval, repetitions, ease, due = row
            table.add_row([
                card_name,
                interval if interval is not None else "-",
                repetitions if repetitions is not None else "-",
                round(ease, 2) if ease is not None else "-",
                due if due is not None else "-"
            ])

    print(table)

def main():
    parser = argparse.ArgumentParser(description="Flashcard CLI")
    parser.add_argument('--deck', type=str, required=True, help="Deck name (JSON file in ./json/ folder without extension)")
    parser.add_argument('--random', action='store_true', help="Show one random card from the deck")
    parser.add_argument('--num', type=int, help="Number of random cards to show sequentially")
    parser.add_argument('--card', type=str, help="Show specific card by exact name")
    parser.add_argument('--search', type=str, help="Print specific card by exact name")
    parser.add_argument('--list', type=str, choices=['all', 'in_db'], help="List cards. Choose either 'all' or 'in_db'")

    args = parser.parse_args()

    deck_name = args.deck
    deck = load_deck(args.deck)
    for card in deck:
        card['deck_name'] = deck_name

    db_path = './db.sqlite'
    if not os.path.exists(db_path):
        create_db(db_path)

    has_num = args.num is not None
    has_card = args.card is not None
    has_list = args.list
    has_random = args.random
    has_search = args.search

    if has_num and (has_card or has_list or has_search):
        print("Error: --num cannot be used with --card, --list, or --search.")
        sys.exit(1)
    if has_list and has_card:
        print("Error: --list and --card cannot be used together.")
        sys.exit(1)
    if has_random and has_card:
        print("Error: --random and --card cannot be used together.")
        sys.exit(1)
    if has_search and has_card:
        print("Error: --search and --card cannot be used together.")
        sys.exit(1)

    if not any([has_num, has_card, has_list, has_random, has_search]):
        card_data, scheduler_card = anki_select_card(deck, db_path)
        display_card(card_data, scheduler_card, db_path)
        return

    if has_num:
        num = args.num
        if num not in range(1, len(deck)):
            print(f"Error: deck {deck_name} has {len(deck)} entries. Num must be between 1 and {len(deck)}.")
            sys.exit(1)
        else:
            if has_random:
                for _ in range(num):
                    card_data, scheduler_card = random_select_card(deck, db_path)
                    display_card(card_data, scheduler_card, db_path)
                return
            else:
                for _ in range(num):
                    card_data, scheduler_card = anki_select_card(deck, db_path)
                    display_card(card_data, scheduler_card, db_path)
                return

    if has_random:
        card_data, scheduler_card = random_select_card(deck, db_path)
        display_card(card_data, scheduler_card, db_path)
        return

    if has_card:
        card_name = args.card
        card_data, scheduler_card = get_card_by_name(deck, card_name, db_path)
        if card_data:
            display_card(card_data, scheduler_card, db_path)
        else:
            print(f"Card named '{card_name}' not found.")
        return

    if has_list:
        list = args.list
        if list == "all":
            list_cards(deck)
        else:
            list_cards_in_db(deck, db_path)
        return

    if has_search:
        card_name = args.search
        search_card(deck, card_name)
        return

if __name__ == "__main__":
    main()
