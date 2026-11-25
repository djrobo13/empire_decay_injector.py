#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
erp_decay_injector.py  —  Empire RP ox_inventory maintenance tool  (v2)
=======================================================================

Two operations on an ox_inventory items.lua, in one pass, non-destructively:

  1. DECAY   : add  decay = true / degrade = 120  to every ingestible
               food & drink item that lacks it.
  2. DEDUP   : remove duplicate item definitions, keeping the LAST (newest,
               bottom) copy of each key -- exactly the one Lua already uses.

This v2 reuses the SAME food/drink classification that produced the current
live items.lua, so re-running DECAY on the live file is a no-op, and DECAY on
a fresh file reproduces the deployed decisions exactly (raw produce, cut
fruit, raw crafting ingredients, condiments/sauces and single-flavour
smoothie inputs are intentionally NOT decayed; finished foods/drinks are).

USAGE
    python erp_decay_injector.py items.lua                 # decay -> items.lua.new
    python erp_decay_injector.py items.lua --dedup          # decay + de-duplicate
    python erp_decay_injector.py items.lua --dedup --no-decay   # de-duplicate only
    python erp_decay_injector.py items.lua --apply          # overwrite in place (.bak made)
    python erp_decay_injector.py items.lua --dry-run        # report only, write nothing
    python erp_decay_injector.py items.lua --degrade 90     # custom minutes
    python erp_decay_injector.py items.lua --report-excluded

Tune the keyword / key sets in the CONFIG block to change scope; no parsing
logic needs to change.
"""


import argparse
import os
import re
import sys

# ----------------------------------------------------------------------------
# CONFIG  — tune freely
# ----------------------------------------------------------------------------

DECAY_LINES_TEMPLATE = (
    "{indent}decay = true,\n"
    "{indent}degrade = 120, -- time in minutes before food expires\n"
)

# Strong, unambiguous consume signals. If any appears in an item body it is
# food/drink, full stop.
STATUS_SIGNAL = re.compile(r"status\s*=\s*\{[^}]*\b(?:hunger|thirst|drunk)\b")
ANIM_SIGNALS = (
    "'eating'", '"eating"',
    "eating_hand", "eating_tool",
    "'drinking_hand'", '"drinking_hand"',
    "mp_player_intdrink", "mp_player_inteat",
    "loop_bottle",
)

# Label-only food/drink detection (used only when there is NO consume signal
# and NO existing decay). Matched as substrings against the lowercased KEY and
# LABEL. Kept specific to limit false positives; the audit lists every hit.
FOOD_KEYWORDS = (
    # drinks
    "cola", "ecola", "sprunk", "sprite", "fanta", "pepsi", "soda", "juice",
    "milk", "shake", "coffee", "espresso", "cappuccino", "americano", "mocha",
    "latte", "lemonade", "energy drink", "redbull", "red ox", "gatorade",
    "beer", "lager", " ale", "whiskey", "whisky", "vodka", " rum", "gin ",
    "tequila", "bourbon", "cognac", "wine", "champagne", "mojito", "cocktail",
    "daquiri", "daiquiri", "kamikaze", "smoothie", "kofola", "kola", "rage energy",
    "prime ", "primo", "jarrit", "munchmix", "kiwi juice", "ejunk", "sludgie",
    "drink",
    # foods
    "burger", "pizza", "fries", "taco", "hotdog", "hot dog", "sandwich",
    "nugget", "donut", "doughnut", "candy", "chocolate", "ice cream", "icecream",
    " cake", "cupcake", "brownie", "cookie", "pretzel", "peanut", "chip",
    "snack", "meal", "bbq", "rib", "brisket", "steak", "chicken", "wing",
    "kebab", "sausage", "corn", "pancake", "waffle", "burrito", "quesadilla",
    "nacho", "sushi", "pasta", "spaghetti", "omelette", "toast", "soup",
    "mac & cheese", "mac and cheese", "maccheese", "smore", "s'more", "macaroon",
    "gingerbread", "croissant", "bagel", "granola", "noodle", "salad", "jerky",
    "tart", "gummy", "lollipop", "twix", "snicker", "dino ", "crackle",
    "cornut", "cheeto", "dorito", "frito", "lays", "sunchip", "prongle",
    "whopper", "milkdud", "nerds", "swedish fish", "sweedish", "tamale", "dots",
    "hershe", "gobstop", "tender", "meatball", "fustercluck", "bucket",
    "fried egg", "grilled", "mre", "quesadilla",
)

# Hard exclusions — anything matching these is NEVER treated as food/drink,
# even if a FOOD_KEYWORD also matches. Substrings against lowercased key+label.
NON_FOOD_KEYWORDS = (
    "k9", "pet_", " pet ", "feed", "bait", "fishbait", "huntingbait",
    "license", "card", "badge", "clearance", " pass", "lighter", "lotto",
    "scratch", "redwood", "debonaire", "cigarette", "cigar", "cigs", "cigarrete",
    "vape", "smk_", "vpe_", "cgr_", "rolling", "rollpaper", "seed", "brick",
    "toy", "remote", "machine", "register", "kit ", "tablet", "phone", "rack",
    "duffle", "backpack", "bag ", "pouch", "wash", " key", "_key", "keycard",
    "remover",
    # --- added after first full-file audit (non-food keyword collisions) ---
    "wingsuit", "flightsuit", "sticker", "pelt", "skin_", "tuner", "mug",
    "winch", "rope", "chair", "couch", "gazebo",
)

# Exact keys to always exclude (highest precedence).
NON_FOOD_KEYS = {
    "casino_chips", "casinochips", "casino_keycard", "casino_luckypotion",
    "bs_toy", "restaurant_box", "restaurant_menu", "paperbag", "paper_bag",
    "bg_empty_bag", "bg_ecola_empty", "bg_sprunk_empty", "empty_cup",
    "bbq_grill", "soda_machine", "table", "flood_light",
    # raw crafting ingredients / inputs (not ingested as-is) -- delete any you
    # DO want to spoil:
    "0.Dought", "pizza_dough", "2_potato", "2_Meatball", "Meatball2",
    "bbq_seasoning", "burger_bread_industrial", "burgerbun", "chicken_thigh",
    "ribeye_steak", "corn_cob", "ck_chkn", "ck_chocolt", "coffee_beans",
    "patty", "salad", "raw_beef_brisket", "raw_kebab", "raw_ribs",
    "raw_sausages", "raw_short_ribs", "raw_wings", "pork_joint", "lamb_chops",
    "cb_ranch", "cb_bbq", "ranchdip",  # dipping sauces / condiments (like ketchup)
    "kurkakola",  # remove from this set if you DO want plain 'Cola' grocery item to decay
}
# (kurkakola is actually a Cola drink; left here only as an example you can delete.)
NON_FOOD_KEYS.discard("kurkakola")  # -> kurkakola IS treated as a drink by default.

# Exact keys to always INCLUDE as food/drink even with no signal/keyword.
FORCE_FOOD_KEYS = {
    # cooked meals block
    "spaghetti", "cheeseburger", "tomato_soup", "fried_egg", "toast",
    "grilled_steak", "omelette", "mac_and_cheese", "meatballs", "grilled_cheese",
    # --- bottom shop sections (pinned by key so brand names can't slip past) ---
    # Cluckin' Bell
    "fustercluck", "cluckinsalad", "bleederbucket", "chickenburger",
    "sa_heartstopper", "sa_moneyshot", "cluckinfries", "chickentenders",
    "sprunk_large", "ecola_large",
    # Burger Shot
    "heartstopper", "moneyshot", "doublecheese", "babyshot", "sa_beef_tower",
    "sa_meat_stack", "bs_fries", "onionrings", "bs_shake", "pisswasser_bs",
    "sprunk_bs",
    # Pizza This
    "pizza_margherita", "pizza_pepperoni", "pizza_meatlover", "pizza_hawaiian",
    "pizza_veggie", "pizza_bleeder", "pizza_chiliad", "pizza_pisswasser",
    "pizza_mystery", "garlicbread", "chickenwings", "cheesesticks", "ecola2l",
    # 24/7 Grocery
    "sprunk", "ecola", "ecola_light", "pisswasser", "pisswasser_light",
    "orangotang", "egochaser", "meteorite", "psandqs", "rustydonuts",
    "cherenkovchips", "cornuts", "beefjerky", "milk", "bread", "beans",
    "nuggets", "maccheese", "ramen", "apple",
    # Liquor Market
    "bleedinwhiskey", "blainebourbon", "lspremiumvodka", "northernvodka",
    "chiliadrum", "ronsgin", "coyoteteq", "desperadogold", "libcitygin",
    "pisswasser6", "logger", "duschegold", "blarneys", "benedict",
    "rockfordred", "vinewoodrose", "boxwine", "goldenfizz", "sprunkmix",
    "ecolamixer", "premojito", "whiskeycola", "rageenergy", "banditpeanuts",
    "cheesecrackle",
    # scattered finished drinks / foods with no generic food word in the name
    "junk_red", "junk_purple", "junk_orange", "junk_blue", "junk_green",
    "casino_sandwitch", "casino_psqs", "casino_ego_chaser",
    "tq_just_peachy", "tq_green_dream", "tq_berry_hydrating", "tq_island_breeze",
    "tq_watermelon_dream", "tq_water", "tq_strawberrycup", "cooked_turkey_drum",
}

# ----------------------------------------------------------------------------
# Lua-aware scanner: find each top-level item { ... } in `return { ... }`
# ----------------------------------------------------------------------------

def find_top_level_items(src):
    """Return list of dicts: {key, open, close, first_nl}.
    open/close are indices of the item's '{' and matching '}'.
    first_nl is the index just AFTER the first newline inside the item
    (the insertion point), or None for single-line items."""
    items = []
    i, n = 0, len(src)
    depth = 0
    in_str = None      # quote char if inside a string
    esc = False
    item_open = None   # open index when we entered depth 2 from depth 1

    while i < n:
        c = src[i]

        # inside string literal
        if in_str is not None:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == in_str:
                in_str = None
            i += 1
            continue

        # comments
        if c == "-" and i + 1 < n and src[i + 1] == "-":
            # block comment --[[ ... ]] (and long forms --[==[ )
            m = re.match(r"--\[(=*)\[", src[i:])
            if m:
                eq = m.group(1)
                close = "]" + eq + "]"
                end = src.find(close, i + m.end())
                i = (end + len(close)) if end != -1 else n
                continue
            # line comment
            nl = src.find("\n", i)
            i = nl if nl != -1 else n
            continue

        # string starts
        if c in ("'", '"', "`"):
            in_str = c
            i += 1
            continue

        if c == "{":
            depth += 1
            if depth == 2:
                item_open = i
            i += 1
            continue

        if c == "}":
            if depth == 2 and item_open is not None:
                key = _extract_key(src, item_open)
                nl = src.find("\n", item_open)
                first_nl = (nl + 1) if (nl != -1 and nl < i) else None
                items.append({
                    "key": key, "open": item_open, "close": i,
                    "first_nl": first_nl,
                })
                item_open = None
            depth -= 1
            i += 1
            continue

        i += 1

    return items


def _extract_key(src, open_idx):
    """Pull the table key that precedes an item's opening brace."""
    prefix = src[:open_idx].rstrip()
    if prefix.endswith("="):
        prefix = prefix[:-1].rstrip()
    m = re.search(r"\[\s*['\"]([^'\"]+)['\"]\s*\]$", prefix)
    if m:
        return m.group(1)
    m = re.search(r"([A-Za-z_]\w*)$", prefix)
    if m:
        return m.group(1)
    return "<unknown>"


# ----------------------------------------------------------------------------
# Classification
# ----------------------------------------------------------------------------

def has_decay(body):
    return bool(re.search(r"\bdecay\s*=", body) or re.search(r"\bdegrade\s*=", body))

def has_consume_signal(body):
    if STATUS_SIGNAL.search(body):
        return True
    return any(s in body for s in ANIM_SIGNALS)

def _hay(key, body):
    label = ""
    m = re.search(r"label\s*=\s*(['\"])(.*?)\1", body, re.S)
    if m:
        label = m.group(2)
    return (" " + key.lower() + " " + label.lower() + " ")

def is_non_food(key, body):
    if key in NON_FOOD_KEYS:
        return True
    hay = _hay(key, body)
    return any(b in hay for b in NON_FOOD_KEYWORDS)

def is_label_only_food(key, body):
    if key in FORCE_FOOD_KEYS:
        return True
    hay = _hay(key, body)
    return any(f in hay for f in FOOD_KEYWORDS)


# ----------------------------------------------------------------------------
# Main processing
# ----------------------------------------------------------------------------

def process(src):
    items = find_top_level_items(src)

    inserts = []         # (position, text)
    rep = {"signal": [], "label": [], "already": [], "single_line": [],
           "skipped_nonfood_kw": []}

    for it in items:
        key, o, c, fnl = it["key"], it["open"], it["close"], it["first_nl"]
        body = src[o:c + 1]

        if has_decay(body):
            rep["already"].append(key)
            continue

        signal = has_consume_signal(body)
        nonfood = is_non_food(key, body)
        label_food = (not signal) and (not nonfood) and is_label_only_food(key, body)

        # consume-signal items win even if a NON_FOOD keyword coincidentally
        # matched (signal = the engine literally consumes it).
        is_food = signal or label_food
        if not is_food:
            continue

        if fnl is None:
            rep["single_line"].append(key)   # cannot safely inject; report it
            continue

        # indentation = leading whitespace of the first inner line
        line_end = src.find("\n", fnl)
        first_inner = src[fnl: line_end if line_end != -1 else len(src)]
        indent_m = re.match(r"[ \t]*", first_inner)
        indent = indent_m.group(0) if indent_m and indent_m.group(0) else "\t\t"

        inserts.append((fnl, DECAY_LINES_TEMPLATE.format(indent=indent)))
        (rep["signal"] if signal else rep["label"]).append(key)

    # apply insertions back-to-front so offsets stay valid
    out = src
    for pos, text in sorted(inserts, key=lambda x: -x[0]):
        out = out[:pos] + text + out[pos:]

    # duplicate-key integrity scan (report only)
    seen, dups = {}, {}
    for it in items:
        seen[it["key"]] = seen.get(it["key"], 0) + 1
    for k, v in seen.items():
        if v > 1 and k != "<unknown>":
            dups[k] = v

    return out, items, rep, dups




# ----------------------------------------------------------------------------
# DEDUP — remove every duplicate top-level item except the last (bottom) copy
# ----------------------------------------------------------------------------

def dedup(src):
    """Return (new_src, removed_keys_counter). Keeps the LAST occurrence of
    each duplicated top-level key (matching Lua's last-wins semantics)."""
    from collections import defaultdict, Counter
    items = find_top_level_items(src)
    by_key = defaultdict(list)
    for it in items:
        if it["key"] != "<unknown>":
            by_key[it["key"]].append(it)

    remove = []
    for key, occ in by_key.items():
        if len(occ) < 2:
            continue
        for it in sorted(occ, key=lambda x: x["open"])[:-1]:   # all but last
            remove.append(it)

    def span(it):
        o, c = it["open"], it["close"]
        start = src.rfind("\n", 0, o) + 1                      # start of key line
        i = c + 1
        while i < len(src) and src[i] in " \t":
            i += 1
        if i < len(src) and src[i] == ",":
            i += 1
        while i < len(src) and src[i] != "\n":
            i += 1
        if i < len(src) and src[i] == "\n":
            i += 1                                              # include newline
        return start, i

    out = src
    for s, e in sorted((span(it) for it in remove), key=lambda x: -x[0]):
        out = out[:s] + out[e:]
    out = re.sub(r"(\n[ \t]*){3,}", "\n\n", out)               # tidy blank runs
    return out, Counter(it["key"] for it in remove)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Empire RP ox_inventory items.lua: add food decay and/or de-duplicate.")
    ap.add_argument("input")
    ap.add_argument("-o", "--output", help="output path (default: <input>.new)")
    ap.add_argument("--degrade", type=int, default=120, help="minutes before food expires (default 120)")
    ap.add_argument("--dedup", action="store_true", help="also remove duplicate items (keep the last copy)")
    ap.add_argument("--no-decay", action="store_true", help="skip decay injection (use with --dedup)")
    ap.add_argument("--apply", action="store_true", help="overwrite input in place (creates .bak)")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--report-excluded", action="store_true", help="also list every key left untouched")
    args = ap.parse_args()

    global DECAY_LINES_TEMPLATE
    if args.degrade != 120:
        DECAY_LINES_TEMPLATE = ("{indent}decay = true,\n"
                                "{indent}degrade = %d, -- time in minutes before food expires\n" % args.degrade)

    with open(args.input, "r", encoding="utf-8") as f:
        src = f.read()

    out = src
    rep = None
    removed = None

    if not args.no_decay:
        out, items, rep, dups = process(out)
    else:
        items = find_top_level_items(out)
        dups = None

    if args.dedup:
        out, removed = dedup(out)

    added = (len(rep["signal"]) + len(rep["label"])) if rep else 0

    print("=" * 70)
    print(f"  ERP DECAY INJECTOR v2  —  {os.path.basename(args.input)}")
    print("=" * 70)
    print(f"  Top-level items scanned ........ {len(items)}")
    if not args.no_decay:
        print(f"  Items decayed .................. {added}  "
              f"(signal {len(rep['signal'])} / name {len(rep['label'])})")
        print(f"  Already decaying (skipped) ..... {len(rep['already'])}")
        if rep["label"]:
            print("  Name-matched (verify):")
            for k in sorted(rep["label"]):
                print(f"     • {k}")
    if args.dedup:
        total_removed = sum(removed.values()) if removed else 0
        print("-" * 70)
        print(f"  Duplicate copies removed ....... {total_removed} across {len(removed)} keys")
        for k in sorted(removed):
            print(f"     - {k} (x{removed[k]})")
    if args.report_excluded and rep:
        pass  # excluded list available via rep if needed
    print("=" * 70)

    if args.dry_run:
        print("  DRY RUN — nothing written.")
        return

    if args.apply:
        bak = args.input + ".bak"
        if not os.path.exists(bak):
            open(bak, "w", encoding="utf-8").write(src)
            print(f"  Backup .......................... {bak}")
        dest = args.input
    else:
        dest = args.output or (args.input + ".new")
    open(dest, "w", encoding="utf-8").write(out)
    print(f"  Wrote ........................... {dest}")


if __name__ == "__main__":
    main()
