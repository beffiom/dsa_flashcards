import argparse
import json
import os
import random
import sys
import sqlite3
import random
import time
from prettytable import PrettyTable
from simple_spaced_repetition import Card, Scheduler, Rating

def load_deck(deck):
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
            due INTEGER,
            FOREIGN KEY(card_id) REFERENCES cards(id)
        )
    ''')

    conn.commit()
    conn.close()

def card_exists_in_db(card_uuid, db_path):
    """
    Check if a card with the given UUID exists in the database.

    Args:
        card_uuid (str): The UUID of the card to check.
        db_path (str): Path to the SQLite database file.

    Returns:
        bool: True if card exists, False otherwise.
    """
    query = "SELECT 1 FROM cards WHERE uuid = ? LIMIT 1"
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
    """
    Adds a new card to the 'cards' table with unique uuid, card_name, and deck_name.

    Args:
        card_uuid (str): Unique identifier for the card (from JSON).
        card_name (str): The name of the card.
        deck_name (str): The deck this card belongs to.
        db_path (str): SQLite database file path.

    Returns:
        int or None: The inserted card id or None if insertion fails.
    """
    insert_query = '''
        INSERT INTO cards (uuid, card_name, deck_name) VALUES (?, ?, ?)
    '''
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(insert_query, (card_uuid, card_name, deck_name))
            conn.commit()
            return cursor.lastrowid
    except sqlite3.IntegrityError as e:
        print(f"Failed to add card with UUID {uuid}: {e}")
        return None
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return None

def update_card_in_db(card_uuid, scheduler, db_path):
    """
    Update the scheduling table for card identified by card_uuid.

    Args:
        card_uuid (str): UUID of the card.
        scheduler (simple_spaced_repetition.Scheduler.Card): The card scheduling info after review.
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

        # Check if scheduling info exists for this card
        cursor.execute("SELECT 1 FROM scheduling WHERE card_id = ?", (card_id,))
        exists = cursor.fetchone()
        
        if exists:
            # Update existing scheduling info
            cursor.execute('''
                UPDATE scheduling
                SET interval = ?, repetitions = ?, ease = ?, due = ?
                WHERE card_id = ?
            ''', (
                scheduler.interval,
                scheduler.repetitions,
                scheduler.ease,
                scheduler.due,
                card_id
            ))
        else:
            # Insert new scheduling info
            cursor.execute('''
                INSERT INTO scheduling (card_id, interval, repetitions, ease, due) 
                VALUES (?, ?, ?, ?, ?)
            ''', (
                card_id,
                scheduler.interval,
                scheduler.repetitions,
                scheduler.ease,
                scheduler.due
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
    import time

    now = int(time.time())
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        # Find card_id with due date <= now (card due for review)
        cursor.execute('''
            SELECT c.card_uuid, c.card_name, s.interval, s.repetitions, s.ease, s.due
            FROM cards c
            JOIN scheduling s ON c.id = s.card_id
            WHERE s.due <= ?
            ORDER BY s.due ASC LIMIT 1
        ''', (now,))
        row = cursor.fetchone()

        if row:
            card_uuid, card_name, interval, repetitions, ease, due = row
            # Locate card in deck by UUID
            card_data = next((c for c in deck if c['card_uuid'] == card_uuid), None)
            if card_data is None:
                print(f"Warning: card_uuid {card_uuid} found in DB but missing in JSON deck")
                # fallback to random
            else:
                scheduler_card = Card(interval=interval, repetitions=repetitions, ease=ease, due=due)
                return card_data, scheduler_card

        # No due cards found, pick a random card from deck
        card_data = random.choice(deck)
        card_uuid = card_data['card_uuid']

        # Check if card exists in cards, insert if not
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
            # Create initial card scheduling (interval=0, repetitions=0, ease=2.5, due=now)
            initial_sched = Card(interval=0, repetitions=0, ease=2.5, due=now)
            cursor.execute(
                "INSERT INTO scheduling (card_id, interval, repetitions, ease, due) VALUES (?, ?, ?, ?, ?)",
                (card_id, initial_sched.interval, initial_sched.repetitions, initial_sched.ease, initial_sched.due)
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
            card_uuid, card_name, interval, repetitions, ease, due = row
            card_data = next((c for c in deck if c['card_uuid'] == card_uuid), None)
            if card_data:
                scheduler_card = Card(interval=interval, repetitions=repetitions, ease=ease, due=due)
                return card_data, scheduler_card
        
        # Fallback: just return the random card with initial scheduling
        return card_data, initial_sched

def display_card(card_data, scheduler_card, db_path):
    """
    Show front (card_name), wait for user, then show back (remaining fields) in PrettyTable.
    Prompt user for difficulty rating and update scheduling in DB.

    Args:
        card_data (dict): The card dict loaded from JSON.
        scheduler_card (simple_spaced_repetition.Card): Current scheduling state.
        db_path (str): Path to database.
    """
    input(f"\nFlashcard: {card_data['card_name']}\nPress Enter to show the back...")

    # Prepare back side data (exclude card_uuid and card_name)
    table = PrettyTable()
    table.field_names = ["Field", "Value"]

    for key, value in card_data.items():
        if key in ('card_uuid', 'card_name'):
            continue
        # Show empty string if value is None
        table.add_row([key, value if value is not None else ""])

    print(table)

    # Ask user to evaluate difficulty
    print("\nEvaluate your recall difficulty:")
    print("1: Again (failed to recall)")
    print("2: Hard")
    print("3: Medium (Good)")
    print("4: Easy")
    while True:
        rating_input = input("Enter 1,2,3 or 4: ").strip()
        if rating_input in ("1", "2", "3", "4"):
            break
        print("Invalid input, please enter 1, 2, 3, or 4.")

    rating_map = {
        "1": Rating.AGAIN,
        "2": Rating.HARD,
        "3": Rating.GOOD,
        "4": Rating.EASY
    }
    rating = rating_map[rating_input]

    # Call evaluate difficulty to update scheduling
    new_sched_card = evaluate_difficulty(scheduler_card, rating)

    # Persist updated scheduling in DB
    update_card_in_db(card_data['card_uuid'], new_sched_card, db_path)

    print("Scheduling updated.")

def evaluate_difficulty(card, rating):
    """
    Given a card and user's recall rating, update card scheduling using simple_spaced_repetition Scheduler.

    Args:
        card (simple_spaced_repetition.Card): the current state of the card.
        rating (simple_spaced_repetition.Rating): user rating from AGAIN, HARD, GOOD, EASY.

    Returns:
        simple_spaced_repetition.Card: updated card scheduling info.
    """
    scheduler = Scheduler()
    new_card = scheduler.review(card, rating)
    return new_card

def random_select_card(deck, db_path):
    """
    Selects a random card from the deck, returns card_data and its scheduling info.
    Initializes DB info if card hasn't been seen before.

    Returns:
        card_data (dict), scheduler_card (Card)
    """
    card_data = random.choice(deck)
    card_uuid = card_data["card_uuid"]

    now = int(time.time())
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        # Get card_id
        cursor.execute("SELECT id FROM cards WHERE card_uuid = ?", (card_uuid,))
        row = cursor.fetchone()
        if not row:
            # Insert into cards table
            cursor.execute(
                "INSERT INTO cards (card_uuid, deck_name, card_name) VALUES (?, ?, ?)",
                (card_uuid, card_data['deck_name'], card_data['card_name'])
            )
            conn.commit()
            card_id = cursor.lastrowid
        else:
            card_id = row[0]

        # Try to get scheduling info
        cursor.execute("SELECT interval, repetitions, ease, due FROM scheduling WHERE card_id = ?", (card_id,))
        sched_row = cursor.fetchone()

        if sched_row:
            interval, repetitions, ease, due = sched_row
            scheduler_card = Card(interval=interval, repetitions=repetitions, ease=ease, due=due)
        else:
            # Create initial scheduling (never seen before)
            scheduler_card = Card(interval=0, repetitions=0, ease=2.5, due=now)
            cursor.execute(
                "INSERT INTO scheduling (card_id, interval, repetitions, ease, due) VALUES (?, ?, ?, ?, ?)",
                (card_id, scheduler_card.interval, scheduler_card.repetitions, scheduler_card.ease, scheduler_card.due)
            )
            conn.commit()

    return card_data, scheduler_card

def get_card_by_name(deck, card_name, db_path):
    """
    Find card by name (case-insensitive) in deck, retrieve its scheduling info.
    If card is not present in DB, adds it and creates default scheduler.

    Returns:
        (card_data, scheduler_card) if found, or (None, None) if not found.
    """
    # Find the card in the deck (case-insensitive match)
    card_data = next(
        (card for card in deck if card['card_name'].lower() == card_name.lower()),
        None
    )
    if not card_data:
        return None, None

    card_uuid = card_data['card_uuid']
    now = int(time.time())

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        # Get or insert card_id
        cursor.execute("SELECT id FROM cards WHERE card_uuid = ?", (card_uuid,))
        row = cursor.fetchone()
        if not row:
            # Card isn't in DB, add it now
            cursor.execute(
                "INSERT INTO cards (card_uuid, deck_name, card_name) VALUES (?, ?, ?)",
                (card_uuid, card_data['deck_name'], card_data['card_name'])
            )
            conn.commit()
            card_id = cursor.lastrowid
        else:
            card_id = row[0]

        # Try to get scheduling info
        cursor.execute("SELECT interval, repetitions, ease, due FROM scheduling WHERE card_id = ?", (card_id,))
        sched_row = cursor.fetchone()

        if sched_row:
            interval, repetitions, ease, due = sched_row
            scheduler_card = Card(interval=interval, repetitions=repetitions, ease=ease, due=due)
        else:
            # Create initial scheduling (never seen before)
            scheduler_card = Card(interval=0, repetitions=0, ease=2.5, due=now)
            cursor.execute(
                "INSERT INTO scheduling (card_id, interval, repetitions, ease, due) VALUES (?, ?, ?, ?, ?)",
                (card_id, scheduler_card.interval, scheduler_card.repetitions, scheduler_card.ease, scheduler_card.due)
            )
            conn.commit()

    return card_data, scheduler_card

def list_cards(deck, db_path):
    """
    Print a table of all card names in the current deck,
    showing some scheduling info from the DB (if present).
    """

    # Map card_uuid to card_name for lookup
    uuid_to_name = {card['card_uuid']: card['card_name'] for card in deck}

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        # Get all cards for this deck in DB
        cursor.execute('''
            SELECT c.card_uuid, c.card_name, s.interval, s.repetitions, s.ease, s.due
            FROM cards c
            LEFT JOIN scheduling s ON c.id = s.card_id
            WHERE c.deck_name = ?
            ORDER BY c.card_name COLLATE NOCASE ASC
        ''', (deck[0]['deck_name'],))  # assuming all cards in 'deck' have the same deck_name

        table = PrettyTable()
        table.field_names = ["Card Name", "Interval", "Repetitions", "Ease", "Due"]

        for row in cursor.fetchall():
            card_uuid, card_name, interval, repetitions, ease, due = row
            # Display values or '-' if not set
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
    parser.add_argument('--list', action='store_true', help="List all card names in the deck")

    args = parser.parse_args()

    deck = load_deck(args.deck)

    db_path = './db.sqlite'
    if os.path.exists(db_path):
        db = db_path
    else:
        create_db(db_path)
        db = db_path

    # Conflict handling
    has_num = args.num is not None
    has_card = args.card is not None
    has_list = args.list
    has_random = args.random
    if has_num and (has_card or has_list):
        print("Error: --num cannot be used with --card or --list.")
        sys.exit(1)
    if has_list and has_card:
        print("Error: --list and --card cannot be used together.")
        sys.exit(1)
    if has_random and has_card:
        print("Error: --random_card and --card cannot be used together.")
        sys.exit(1)

    # Default use case
    if not any([has_num, has_card, has_list, has_random]):
        card_data, scheduler_card = anki_select_card(deck, db)
        display_card(card_data, scheduler_card, db)
        return

    if has_num:
        num = args.num  # expected to be int
    
        if has_random:
            for _ in range(num):
                card_data, scheduler_card = random_select_card(deck, db)
                display_card(card_data, scheduler_card, db)
            return
    
        else:
            for _ in range(num):
                card_data, scheduler_card = anki_select_card(deck, db)
                display_card(card_data, scheduler_card, db)
            return

    if has_random:
        card_data, scheduler_card = random_select_card(deck, db)
        display_card(card_data, scheduler_card, db)
        return

    if has_card:
        card_name = args.card
        card_data, scheduler_card = get_card_by_name(deck, card_name, db)
        if card:
            display_card(card_data, scheduler_card, db)
        else:
            print(f"Card named '{card_name}' not found.")
        return

    if has_list:
        list_cards(deck, db)
        return

if __name__ == "__main__":
    main()
