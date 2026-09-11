"""Server-owned world state and its shared validated action boundary."""

import copy
import json
import os
from pathlib import Path
import tempfile
import threading


MAX_MESSAGE_LENGTH = 500
MAX_SOCIAL_VALUE = 5
TALK_INTENTS = {"neutral", "confide", "encourage", "insult", "report_help", "report_harm"}
REPORT_INTENTS = {"report_help", "report_harm"}


def starter_world():
    return {
        "schema_version": 2,
        "clock": {"tick": 0, "running": False},
        "locations": {
            "street": {"kind": "street", "name": "Main Street"},
            "home-1": {"kind": "home", "name": "Home One"},
            "home-2": {"kind": "home", "name": "Home Two"},
            "shop": {"kind": "shop", "name": "Corner Shop"},
        },
        "paths": {
            f"path-{place}": {"from": "street", "to": place, "bidirectional": True}
            for place in ("home-1", "home-2", "shop")
        },
        "citizens": {
            f"citizen-{i}": {
                "name": name, "location_id": home, "home_id": home,
                "food": 0, "money": 100,
            }
            for i, name, home in (
                (1, "Ada", "home-1"), (2, "Ben", "home-1"), (3, "Cleo", "home-2")
            )
        },
        "shop": {"location_id": "shop", "food": 30, "money": 0},
        "events": [{"id": 1, "tick": 0, "type": "world_created", "entity_ids": [], "details": {}}],
    }


def validate(state, require_private_events=False):
    """Reject unsupported or inconsistent snapshots instead of silently reseeding."""
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def natural(value):
        return type(value) is int and value >= 0

    require(state["schema_version"] in (1, 2), "Unsupported save schema")
    clock = state["clock"]
    require(natural(clock["tick"]) and type(clock["running"]) is bool, "Invalid clock")
    locations = state["locations"]
    require(bool(locations), "Missing locations")
    for path in state["paths"].values():
        require(path["from"] in locations and path["to"] in locations, "Invalid path reference")
        require(type(path["bidirectional"]) is bool, "Invalid path direction")
    for citizen in state["citizens"].values():
        require(citizen["location_id"] in locations, "Invalid citizen location")
        require(citizen["home_id"] in locations, "Invalid home reference")
        require(locations[citizen["home_id"]]["kind"] == "home", "Home must reference a home")
    require(state["shop"]["location_id"] in locations, "Invalid shop reference")
    require(locations[state["shop"]["location_id"]]["kind"] == "shop", "Shop must reference a shop")
    for owner in [*state["citizens"].values(), state["shop"]]:
        require(natural(owner["food"]) and natural(owner["money"]), "Invalid resources")
    previous_id, previous_tick = 0, 0
    for event in state["events"]:
        require(type(event["id"]) is int and event["id"] > previous_id, "Invalid event order")
        require(natural(event["tick"]) and previous_tick <= event["tick"] <= clock["tick"], "Invalid event time")
        require(isinstance(event["type"], str) and isinstance(event["details"], dict), "Invalid event")
        require(isinstance(event["entity_ids"], list), "Invalid event entities")
        if event["type"] == "citizen_talked":
            require(event["details"] == {}, "Talk events cannot contain private details")
            require(
                len(event["entity_ids"]) == 2
                and all(citizen_id in state["citizens"] for citizen_id in event["entity_ids"])
                and event["entity_ids"][0] != event["entity_ids"][1],
                "Invalid talk event entities",
            )
        previous_id, previous_tick = event["id"], event["tick"]

    private_social = state.get("private_social", {})
    require(state["schema_version"] == 2 or not private_social, "Schema 1 cannot contain social state")
    require(isinstance(private_social, dict), "Invalid private social state")
    event_by_id = {event["id"]: event for event in state["events"]}
    citizens = state["citizens"]
    memory_event_ids = set()
    for owner_id, social in private_social.items():
        require(owner_id in citizens and isinstance(social, dict), "Invalid social state owner")
        require(set(social) == {"memories", "relationships", "beliefs"}, "Invalid social state")
        memories = social["memories"]
        relationships = social["relationships"]
        beliefs = social["beliefs"]
        require(isinstance(memories, list), "Invalid memories")
        require(isinstance(relationships, dict), "Invalid relationships")
        require(isinstance(beliefs, dict), "Invalid beliefs")

        previous_memory_id = 0
        for memory in memories:
            require(isinstance(memory, dict), "Invalid memory")
            require(type(memory.get("id")) is int and memory["id"] > previous_memory_id, "Invalid memory order")
            require(natural(memory.get("tick")) and memory["tick"] <= clock["tick"], "Invalid memory time")
            require(memory.get("type") == "message_received", "Invalid memory type")
            require(memory.get("actor_id") in citizens, "Invalid memory actor")
            message = memory.get("message")
            require(
                isinstance(message, str) and message.strip() and len(message) <= MAX_MESSAGE_LENGTH,
                "Invalid memory message",
            )
            intent = memory.get("intent")
            require(intent in TALK_INTENTS, "Invalid memory intent")
            subject_id = memory.get("subject_id")
            require((intent in REPORT_INTENTS) == (subject_id is not None), "Invalid memory subject")
            if subject_id is not None:
                require(subject_id in citizens and subject_id != owner_id, "Invalid memory subject")
            event = event_by_id.get(memory["id"])
            require(
                event is not None
                and event["type"] == "citizen_talked"
                and event["tick"] == memory["tick"]
                and event["entity_ids"] == [memory["actor_id"], owner_id],
                "Memory does not match its public event",
            )
            memory_event_ids.add(memory["id"])
            previous_memory_id = memory["id"]

        for subject_id, values in relationships.items():
            require(subject_id in citizens and subject_id != owner_id, "Invalid relationship subject")
            require(
                isinstance(values, dict) and set(values) == {"trust", "friendship", "anger"},
                "Invalid relationship",
            )
            require(
                all(type(value) is int and 0 <= value <= MAX_SOCIAL_VALUE for value in values.values()),
                "Invalid relationship value",
            )

        for subject_id, belief in beliefs.items():
            require(subject_id in citizens and subject_id != owner_id, "Invalid belief subject")
            require(
                isinstance(belief, dict)
                and set(belief) == {"helpful"}
                and type(belief["helpful"]) is bool,
                "Invalid belief",
            )
    if require_private_events:
        require(
            all(
                event["type"] != "citizen_talked" or event["id"] in memory_event_ids
                for event in state["events"]
            ),
            "Talk event is missing its private recipient memory",
        )


def migrate(state):
    """Return a validated current-schema copy without discarding old world data."""
    validate(state, require_private_events=state.get("schema_version") == 2)
    if state["schema_version"] == 2:
        return copy.deepcopy(state)
    candidate = copy.deepcopy(state)
    candidate["schema_version"] = 2
    validate(candidate, require_private_events=True)
    return candidate


def write_snapshot(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(state, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class World:
    def __init__(self, save_path):
        self._path = Path(save_path)
        self._lock = threading.Lock()
        if self._path.exists():
            with self._path.open(encoding="utf-8") as handle:
                loaded = json.load(handle)
            self._state = migrate(loaded)
            if self._state != loaded:
                write_snapshot(self._path, self._state)
        else:
            self._state = starter_world()
            self.save()

    def snapshot(self):
        """Return an isolated public copy with all private social state removed."""
        with self._lock:
            public = copy.deepcopy(self._state)
            public.pop("private_social", None)
            return public

    def citizen_context(self, citizen_id):
        """Return one citizen's detached private context for trusted server-side use."""
        with self._lock:
            if citizen_id not in self._state["citizens"]:
                raise ValueError("Unknown citizen")
            social = self._state.get("private_social", {}).get(citizen_id)
            if social is None:
                social = {"memories": [], "relationships": {}, "beliefs": {}}
            return copy.deepcopy(social)

    def save(self):
        with self._lock:
            validate(self._state, require_private_events=True)
            write_snapshot(self._path, self._state)

    def _change(self, kind, *, running=None, ticks=0, only_if_running=False):
        with self._lock:
            if only_if_running and not self._state["clock"]["running"]:
                return
            if running is not None and self._state["clock"]["running"] == running:
                return
            candidate = copy.deepcopy(self._state)
            candidate["clock"]["tick"] += ticks
            if running is not None:
                candidate["clock"]["running"] = running
            events = candidate["events"]
            events.append({
                "id": events[-1]["id"] + 1 if events else 1,
                "tick": candidate["clock"]["tick"], "type": kind,
                "entity_ids": [], "details": {"ticks": ticks} if ticks else {},
            })
            validate(candidate, require_private_events=True)
            write_snapshot(self._path, candidate)
            self._state = candidate

    def start(self):
        self._change("clock_started", running=True)

    def pause(self):
        self._change("clock_paused", running=False)

    def advance(self, ticks=1):
        """Explicit deterministic advancement, including while paused."""
        if type(ticks) is not int or ticks < 0:
            raise ValueError("ticks must be a nonnegative integer")
        if ticks:
            self._change("time_advanced", ticks=ticks)

    def tick_if_running(self):
        self._change("time_advanced", ticks=1, only_if_running=True)

    def act(self, actor_id, action_name, target_id, message, params=None):
        """Validate and atomically apply an action shared by every kind of caller."""
        with self._lock:
            citizens = self._state["citizens"]
            if not isinstance(actor_id, str) or actor_id not in citizens:
                raise ValueError("Unknown actor")
            if not isinstance(target_id, str) or target_id not in citizens:
                raise ValueError("Unknown target")
            if actor_id == target_id:
                raise ValueError("A citizen cannot target themselves with talk")
            if not isinstance(action_name, str) or action_name != "talk":
                raise ValueError("Unsupported action")
            if not isinstance(message, str) or not message.strip():
                raise ValueError("Message must contain text")
            if len(message) > MAX_MESSAGE_LENGTH:
                raise ValueError(f"Message must be at most {MAX_MESSAGE_LENGTH} characters")
            if params is None:
                params = {}
            if not isinstance(params, dict):
                raise ValueError("Action parameters must be a dictionary")
            if not set(params) <= {"intent", "subject_id"}:
                raise ValueError("Unsupported action parameter")
            intent = params.get("intent", "neutral")
            if not isinstance(intent, str) or intent not in TALK_INTENTS:
                raise ValueError("Unsupported talk intent")
            subject_id = params.get("subject_id")
            if intent in REPORT_INTENTS:
                if not isinstance(subject_id, str) or subject_id not in citizens:
                    raise ValueError("Report intent requires a known subject citizen")
                if subject_id == target_id:
                    raise ValueError("A citizen cannot receive a belief about themselves")
            elif subject_id is not None:
                raise ValueError("This talk intent does not accept a subject")

            candidate = copy.deepcopy(self._state)
            event_id = candidate["events"][-1]["id"] + 1 if candidate["events"] else 1
            tick = candidate["clock"]["tick"]
            social = candidate.setdefault("private_social", {}).setdefault(
                target_id, {"memories": [], "relationships": {}, "beliefs": {}}
            )
            memory = {
                "id": event_id,
                "tick": tick,
                "type": "message_received",
                "actor_id": actor_id,
                "message": message,
                "intent": intent,
            }
            if subject_id is not None:
                memory["subject_id"] = subject_id
            social["memories"].append(memory)

            relationship_subject = actor_id
            changed_value = None
            if intent == "confide":
                changed_value = "trust"
            elif intent == "encourage":
                changed_value = "friendship"
            elif intent == "insult":
                changed_value = "anger"
            elif intent in REPORT_INTENTS:
                relationship_subject = subject_id
                changed_value = "trust" if intent == "report_help" else "anger"
                social["beliefs"][subject_id] = {"helpful": intent == "report_help"}
            if changed_value is not None:
                relationship = social["relationships"].setdefault(
                    relationship_subject, {"trust": 0, "friendship": 0, "anger": 0}
                )
                relationship[changed_value] = min(
                    MAX_SOCIAL_VALUE, relationship[changed_value] + 1
                )

            event = {
                "id": event_id,
                "tick": tick,
                "type": "citizen_talked",
                "entity_ids": [actor_id, target_id],
                "details": {},
            }
            candidate["events"].append(event)
            validate(candidate, require_private_events=True)
            write_snapshot(self._path, candidate)
            self._state = candidate
            return copy.deepcopy(event)
