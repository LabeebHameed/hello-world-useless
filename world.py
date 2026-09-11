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
SCHEMA_VERSION = 3
HOURS_PER_DAY = 24
LOW_ENERGY = 25
RESTED_ENERGY = 60
ACTIVITIES = {"sleeping", "travelling", "resting", "at_regular_destination"}


def starter_world():
    return {
        "schema_version": SCHEMA_VERSION,
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
                "needs": {"hunger": 0, "energy": 100},
                "schedule": {
                    "regular_destination_id": "shop",
                    "leave_home_hour": 8,
                    "return_home_hour": 18,
                    "sleep_hour": 22,
                    "wake_hour": 6,
                },
                "activity": "resting",
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

    require(state["schema_version"] in (1, 2, 3), "Unsupported save schema")
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
        if state["schema_version"] == 3:
            needs = citizen.get("needs")
            require(
                isinstance(needs, dict) and set(needs) == {"hunger", "energy"},
                "Invalid citizen needs",
            )
            require(
                all(type(value) is int and 0 <= value <= 100 for value in needs.values()),
                "Invalid need value",
            )
            schedule = citizen.get("schedule")
            require(
                isinstance(schedule, dict)
                and set(schedule) == {
                    "regular_destination_id", "leave_home_hour", "return_home_hour",
                    "sleep_hour", "wake_hour",
                },
                "Invalid citizen schedule",
            )
            require(
                schedule["regular_destination_id"] in locations
                and schedule["regular_destination_id"] != citizen["home_id"],
                "Invalid regular destination",
            )
            require(
                all(
                    type(schedule[field]) is int and 0 <= schedule[field] < HOURS_PER_DAY
                    for field in ("leave_home_hour", "return_home_hour", "sleep_hour", "wake_hour")
                ),
                "Invalid schedule hour",
            )
            require(schedule["leave_home_hour"] < schedule["return_home_hour"], "Invalid away schedule")
            require(citizen.get("activity") in ACTIVITIES, "Invalid citizen activity")
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
        if event["type"] == "citizen_moved":
            require(
                len(event["entity_ids"]) == 1 and event["entity_ids"][0] in state["citizens"],
                "Invalid move event actor",
            )
            require(
                set(event["details"]) == {"from_location_id", "to_location_id"}
                and event["details"]["from_location_id"] in locations
                and event["details"]["to_location_id"] in locations,
                "Invalid move event details",
            )
        previous_id, previous_tick = event["id"], event["tick"]

    private_social = state.get("private_social", {})
    require(state["schema_version"] in (2, 3) or not private_social, "Schema 1 cannot contain social state")
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
    validate(state, require_private_events=state.get("schema_version") in (2, 3))
    if state["schema_version"] == SCHEMA_VERSION:
        return copy.deepcopy(state)
    candidate = copy.deepcopy(state)
    candidate["schema_version"] = SCHEMA_VERSION
    for citizen in candidate["citizens"].values():
        citizen["needs"] = {"hunger": 0, "energy": 100}
        citizen["schedule"] = {
            "regular_destination_id": candidate["shop"]["location_id"],
            "leave_home_hour": 8,
            "return_home_hour": 18,
            "sleep_hour": 22,
            "wake_hour": 6,
        }
        citizen["activity"] = "resting"
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
        self._lock = threading.RLock()
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
        """Explicit deterministic hourly advancement, including while paused."""
        if type(ticks) is not int or ticks < 0:
            raise ValueError("ticks must be a nonnegative integer")
        for _ in range(ticks):
            self._advance_one_tick()
        if ticks:
            with self._lock:
                candidate = copy.deepcopy(self._state)
                events = candidate["events"]
                events.append({
                    "id": events[-1]["id"] + 1 if events else 1,
                    "tick": candidate["clock"]["tick"],
                    "type": "time_advanced",
                    "entity_ids": [],
                    "details": {"ticks": ticks},
                })
                validate(candidate, require_private_events=True)
                write_snapshot(self._path, candidate)
                self._state = candidate

    def tick_if_running(self):
        self._advance_one_tick(only_if_running=True)

    @staticmethod
    def _is_sleep_hour(hour, schedule):
        sleep_hour = schedule["sleep_hour"]
        wake_hour = schedule["wake_hour"]
        if sleep_hour < wake_hour:
            return sleep_hour <= hour < wake_hour
        return hour >= sleep_hour or hour < wake_hour

    @staticmethod
    def _connected_destination(state, source_id, destination_id):
        for path in state["paths"].values():
            if path["from"] == source_id and path["to"] == destination_id:
                return True
            if path["bidirectional"] and path["to"] == source_id and path["from"] == destination_id:
                return True
        return False

    @classmethod
    def _next_step(cls, state, source_id, destination_id):
        """Return the first hop on a deterministic shortest route."""
        if source_id == destination_id:
            return None
        queue = [(source_id, None)]
        visited = {source_id}
        while queue:
            location_id, first_step = queue.pop(0)
            neighbors = set()
            for path in state["paths"].values():
                if path["from"] == location_id:
                    neighbors.add(path["to"])
                if path["bidirectional"] and path["to"] == location_id:
                    neighbors.add(path["from"])
            for neighbor in sorted(neighbors):
                if neighbor in visited:
                    continue
                step = neighbor if first_step is None else first_step
                if neighbor == destination_id:
                    return step
                visited.add(neighbor)
                queue.append((neighbor, step))
        return None

    @classmethod
    def choose_action(cls, state, citizen_id, private_social=None):
        """Choose one inspectable movement action from a detached state."""
        citizen = state["citizens"][citizen_id]
        schedule = citizen["schedule"]
        location_id = citizen["location_id"]
        home_id = citizen["home_id"]
        hour = state["clock"]["tick"] % HOURS_PER_DAY
        energy = citizen["needs"]["energy"]

        if energy <= LOW_ENERGY or (
            location_id == home_id
            and citizen["activity"] == "resting"
            and energy < RESTED_ENERGY
        ):
            destination_id = home_id
        elif cls._is_sleep_hour(hour, schedule) or not (
            schedule["leave_home_hour"] <= hour < schedule["return_home_hour"]
        ):
            destination_id = home_id
        else:
            destination_id = schedule["regular_destination_id"]
            relationships = (private_social or {}).get("relationships", {})
            strongly_distrusted = {
                subject_id
                for subject_id, values in relationships.items()
                if values.get("anger") == MAX_SOCIAL_VALUE and values.get("trust") == 0
            }
            if any(
                other_id in strongly_distrusted
                and other["location_id"] == destination_id
                for other_id, other in state["citizens"].items()
            ):
                destination_id = home_id

        next_location = cls._next_step(state, location_id, destination_id)
        if next_location is None:
            return None
        return {
            "actor_id": citizen_id,
            "action_name": "move",
            "target_id": next_location,
        }

    def _advance_one_tick(self, only_if_running=False):
        with self._lock:
            if only_if_running and not self._state["clock"]["running"]:
                return
            candidate = copy.deepcopy(self._state)
            candidate["clock"]["tick"] += 1
            hour = candidate["clock"]["tick"] % HOURS_PER_DAY
            for citizen in candidate["citizens"].values():
                needs = citizen["needs"]
                needs["hunger"] = min(100, needs["hunger"] + 1)
                at_home = citizen["location_id"] == citizen["home_id"]
                sleeping = at_home and self._is_sleep_hour(hour, citizen["schedule"])
                if at_home:
                    needs["energy"] = min(100, needs["energy"] + (8 if sleeping else 4))
                else:
                    needs["energy"] = max(0, needs["energy"] - 3)
                if sleeping:
                    citizen["activity"] = "sleeping"
                elif at_home:
                    citizen["activity"] = "resting"
                elif citizen["location_id"] == citizen["schedule"]["regular_destination_id"]:
                    citizen["activity"] = "at_regular_destination"
                else:
                    citizen["activity"] = "travelling"
            validate(candidate, require_private_events=True)
            write_snapshot(self._path, candidate)
            self._state = candidate
            for citizen_id in sorted(candidate["citizens"]):
                decision_state = copy.deepcopy(self._state)
                social = copy.deepcopy(
                    self._state.get("private_social", {}).get(
                        citizen_id, {"memories": [], "relationships": {}, "beliefs": {}}
                    )
                )
                action = self.choose_action(decision_state, citizen_id, social)
                if action is not None:
                    self.act(**action)

    def act(self, actor_id, action_name, target_id=None, message=None, params=None):
        """Validate and atomically apply an action shared by every kind of caller."""
        with self._lock:
            citizens = self._state["citizens"]
            if not isinstance(actor_id, str) or actor_id not in citizens:
                raise ValueError("Unknown actor")
            if not isinstance(action_name, str) or action_name not in {"talk", "move"}:
                raise ValueError("Unsupported action")
            if action_name == "move":
                if not isinstance(target_id, str) or target_id not in self._state["locations"]:
                    raise ValueError("Unknown destination")
                if message is not None:
                    raise ValueError("Move does not accept a message")
                if params is None:
                    params = {}
                if not isinstance(params, dict) or params:
                    raise ValueError("Move does not accept parameters")
                source_id = citizens[actor_id]["location_id"]
                if source_id == target_id:
                    raise ValueError("Citizen is already at destination")
                if not self._connected_destination(self._state, source_id, target_id):
                    raise ValueError("Destination is not connected to current location")

                candidate = copy.deepcopy(self._state)
                candidate["citizens"][actor_id]["location_id"] = target_id
                schedule = candidate["citizens"][actor_id]["schedule"]
                if target_id == schedule["regular_destination_id"]:
                    candidate["citizens"][actor_id]["activity"] = "at_regular_destination"
                elif target_id == candidate["citizens"][actor_id]["home_id"]:
                    candidate["citizens"][actor_id]["activity"] = "resting"
                else:
                    candidate["citizens"][actor_id]["activity"] = "travelling"
                events = candidate["events"]
                event = {
                    "id": events[-1]["id"] + 1 if events else 1,
                    "tick": candidate["clock"]["tick"],
                    "type": "citizen_moved",
                    "entity_ids": [actor_id],
                    "details": {"from_location_id": source_id, "to_location_id": target_id},
                }
                events.append(event)
                validate(candidate, require_private_events=True)
                write_snapshot(self._path, candidate)
                self._state = candidate
                return copy.deepcopy(event)

            if not isinstance(target_id, str) or target_id not in citizens:
                raise ValueError("Unknown target")
            if actor_id == target_id:
                raise ValueError("A citizen cannot target themselves with talk")
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
