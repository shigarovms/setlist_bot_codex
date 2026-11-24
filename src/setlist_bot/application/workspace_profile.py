"""Own profile and preferences, shared with the Telegram bot."""

from typing import Any

from setlist_bot.application.activity import record_activity
from setlist_bot.application.ports import Store
from setlist_bot.application.profiles import Profiles
from setlist_bot.application.role_dictionary import RoleDictionary
from setlist_bot.domain.entities import User
from setlist_bot.domain.export_settings import FONTS, settings, validated_settings
from setlist_bot.domain.rules import require
from setlist_bot.domain.staff_roles import GENERAL_SOUND

PROFILE_KIND = "miniapp_profile_action"
TEXT_LIMITS = {"name": 200, "city": 200, "about": 1000}
PREFERENCES = {"substitutions", "travel"}
FIELDS = {*TEXT_LIMITS, *PREFERENCES, "roles"}


def role_source(value: Any) -> str:
    require(isinstance(value, str) and len(value) <= 5000, "input")
    assert isinstance(value, str)
    return value.strip()


class WorkspaceProfile:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user

    def view(self) -> dict[str, Any]:
        return {
            "language": self.user.language,
            "version": self.user.profile_version,
            "profile": {key: value for key, value in self.user.profile.items() if key in FIELDS},
            "pdf": {"settings": settings(self.user.export_settings), "fonts": FONTS},
        }

    async def roles(self, source: str, language: str) -> list[dict[str, Any]]:
        if not source:
            return []
        index = await RoleDictionary(self.store).index()
        roles = []
        for name in index.local_names(source):
            title, choices, sound = index.profile_choice(name)
            if title is None:
                choices.append(GENERAL_SOUND[language] if sound else name)
            roles.append({"source": name, "title": title, "choices": choices, "sound": sound})
        return roles

    async def preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        version = payload.get("version")
        require(type(version) is int and version == self.user.profile_version, "conflict")
        source = role_source(payload.get("source"))
        language = self.user.language
        return {"roles": await self.roles(source, language), "language": language}

    @staticmethod
    def command(payload: dict[str, Any]) -> dict[str, Any]:
        action = payload.get("action")
        require(action in ("profile_save", "language", "pdf_settings"), "input")
        if action == "language":
            require(
                payload.get("language") in ("ru", "en") and payload.get("expected") in ("ru", "en"),
                "input",
            )
            return {
                "action": action,
                "language": payload["language"],
                "expected": payload["expected"],
            }
        if action == "pdf_settings":
            return {
                "action": action,
                "settings": validated_settings(payload.get("settings")),
                "expected": validated_settings(payload.get("expected")),
            }
        version = payload.get("version")
        require(type(version) is int, "input")
        patch = payload.get("patch")
        require(isinstance(patch, dict) and set(patch) <= (FIELDS - {"roles"}), "input")
        assert isinstance(patch, dict)
        for key, value in patch.items():
            if key in TEXT_LIMITS:
                require(
                    isinstance(value, str)
                    and len(value) <= TEXT_LIMITS[key]
                    and all(
                        ord(char) >= 32 or key == "about" and char in "\r\n\t" for char in value
                    ),
                    "input",
                )
            else:
                require(value is None or type(value) is bool, "input")
        command = {
            "action": action,
            "version": version,
            "patch": {
                key: value.strip() if isinstance(value, str) else value
                for key, value in patch.items()
            },
        }
        if "roles" in payload:
            data = payload["roles"]
            require(isinstance(data, dict), "input")
            source = role_source(data.get("source"))
            choices, language = data.get("choices"), data.get("language")
            require(
                language in ("ru", "en")
                and isinstance(choices, list)
                and len(choices) <= 40
                and all(isinstance(value, str) and 0 < len(value) <= 100 for value in choices),
                "input",
            )
            command["roles"] = {"source": source, "choices": choices, "language": language}
        require(bool(patch) or "roles" in command, "input")
        return command

    async def apply(self, data: dict[str, Any]) -> dict[str, Any]:
        user = await self.store.get(User, self.user.id, lock=True)
        action = data["action"]
        if action == "language":
            require(user.language == data["expected"], "conflict")
            user.language = data["language"]
            record_activity(self.store, user, "language", user.id)
            return {"language": user.language}
        if action == "pdf_settings":
            require(settings(user.export_settings) == data["expected"], "conflict")
            user.export_settings = data["settings"]
            record_activity(self.store, user, "pdf_settings", user.id)
            return {"settings": user.export_settings}
        require(user.profile_version == data["version"], "conflict")
        values = {key: value for key, value in user.profile.items() if key in FIELDS}
        for key, value in data["patch"].items():
            if value is None:
                values.pop(key, None)
            else:
                values[key] = value
        if roles := data.get("roles"):
            plan = await self.roles(roles["source"], roles["language"])
            choices = roles["choices"]
            require(
                len(plan) == len(choices)
                and all(
                    chosen in row["choices"] if row["title"] is None else chosen == row["title"]
                    for row, chosen in zip(plan, choices, strict=True)
                ),
                "input_expired",
            )
            values["roles"] = choices
        saved = await Profiles(self.store, user).save(data["version"], values)
        return {"version": saved.profile_version}
