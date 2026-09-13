# Sample SavedVariables

Sanitized captures used as fixtures for companion development. The importer is
built and tested against these, not against a live WoW installation, so the
tests do not require the game to be installed.

## Sanitizing

Never copy a capture here by hand. Use the script, which replaces player
identity with placeholders and refuses to write the file if any trace survives:

```text
python tools/sanitize-savedvariables.py <path to AzerothChronicle.lua> samples/savedvariables/<name>.lua
```

Player character name, realm, and character GUID are replaced. The character
name is also substituted inside quest and completion text, because the client
writes it into the narrative itself.

NPC names and creature GUIDs are kept on purpose. They are game content rather
than player data, and the importer's NPC identity handling cannot be tested
without them.

Never commit an unsanitized personal history file, an account identifier, or an
entire WTF directory.

## Fixtures

### shadowglen-quest-456.lua

Classic Era 1.15.9, build 69722, interface 11509. A level 1 to 2 night elf
hunter in Shadowglen, Teldrassil. 11 events across several sessions.

Covers:

- A complete quest lifecycle for quest 456, "The Balance of Nature": detail,
  accepted, two progress events, complete, and turned in with 170 XP and 35
  copper.
- A second quest detail, 458 "The Woodland Protector", for testing more than
  one quest and more than one giver.
- Four gossip captures, including one with a structured active quest list and
  several with flavor text only.
- Two quest giver NPCs with creature GUIDs, for NPC identity work.
- Full position on every event: zone, subzone, map id, and coordinates.
- Multiple sessions, including empty ones from reloads, which the importer must
  handle without treating them as errors.

Known gaps, deliberately: no item or book text, and no world dialogue. Gossip
options and available quests are empty throughout, which reflects real client
behavior rather than a capture failure. See the beta validation checklist.
