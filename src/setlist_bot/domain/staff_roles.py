"""RU: Профессия не равна правам доступа. EN: Exact multilingual aliases, no guessed skills.

A broad search may include subroles; a specific booking requires an explicitly declared
skill or a local assignment in that role. Never infer MON competence from FOH experience.
"""

import re

from setlist_bot.domain.rules import normalize, require

# Stable keys are independent of user spelling. Display names remain in StaffRole.name.
ROLES = {
    "guitar": ("Гитара", "гитарист|гитаристка|guitar|guitarist|guitar player"),
    "bass": ("Бас-гитара", "бас|басист|басистка|bass|bassist|bass player|bass guitar"),
    "drums": ("Ударные", "барабаны|барабанщик|ударник|барабанщица|drums|drummer"),
    "percussion": ("Перкуссия", "перкуссионист|percussion|percussionist"),
    "keys": (
        "Клавишные",
        "клавиши|клавишник|клавишница|пианист|keyboard|keys|keyboard player|pianist",
    ),
    "vocals": ("Вокал", "вокалист|вокалистка|певец|певица|vocals|singer|vocalist|lead vocals"),
    "backing_vocals": (
        "Бэк-вокал",
        "бэки|бек-вокал|бэк вокал|бэк-вокалист|бэк-вокалистка|backing vocals|backing singer|bvox",
    ),
    "dj": ("Диджей", "dj|deejay|диджейка"),
    "sax": ("Саксофон", "саксофонист|sax|saxophone|saxophonist"),
    "trumpet": ("Труба", "трубач|trumpet|trumpeter"),
    "trombone": ("Тромбон", "тромбонист|trombone|trombonist"),
    "violin": ("Скрипка", "скрипач|скрипачка|violin|violinist"),
    "cello": ("Виолончель", "виолончелист|cello|cellist"),
    "sound": ("Звуковик", "звук|звукарь|звукорежиссер|sound engineer|audio engineer"),
    "foh": (
        "Звукорежиссёр FOH",
        "foh|фох|foh engineer|front of house|front of house engineer|звукорежиссер фох",
    ),
    "mon": (
        "Мониторный звукорежиссёр",
        "мониторщик|мон|mon|mon engineer|monitor engineer|"
        "мониторный звукорежиссер|мониторный инженер",
    ),
    "audio_tech": ("Звуковой техник", "техник по звуку|audio technician|sound tech|audio tech"),
    "system": (
        "Системный инженер по звуку",
        "системщик по звуку|audio system engineer|system engineer",
    ),
    "rf": ("Радиотехник", "техник радиосистем|rf technician|rf engineer|радиоинженер"),
    "playback": ("Инженер плейбэка", "плейбэк|плейбек|плейбэк-инженер|playback|playback engineer"),
    "backline": (
        "Бэклайн-техник",
        "бэклайн|беклайн|техник по инструментам|backline|backline technician",
    ),
    "guitar_tech": ("Гитарный техник", "гитарный тех|guitar tech|guitar technician"),
    "drum_tech": ("Барабанный техник", "барабанный тех|drum tech|drum technician"),
    "lighting": ("Художник по свету", "световик|свет|lighting designer|ld"),
    "lighting_operator": (
        "Оператор света",
        "оператор светового пульта|lighting operator|light operator",
    ),
    "lighting_tech": ("Световой техник", "техник по свету|lighting technician|lighting tech"),
    "video": ("Видеоинженер", "инженер по видео|video engineer|video technician"),
    "vj": ("Виджей", "vj|видеооператор контента"),
    "tour": ("Тур-менеджер", "турик|турменеджер|тур менеджер|tour manager"),
    "production": ("Продакшн-менеджер", "продакшн менеджер|production manager"),
    "stage": ("Стейдж-менеджер", "стейдж|менеджер сцены|stage manager"),
    "director": ("Концертный директор", "директор коллектива|concert director"),
    "administrator": ("Администратор коллектива", "администратор группы|band administrator"),
    "rigger": ("Риггер", "такелажник|rigger|rigging technician"),
    "stagehand": ("Рабочий сцены", "сценический рабочий|stagehand|сценраб"),
    "dancer": ("Танцор", "танцовщик|танцовщица|танцорка|dancer"),
    "choreographer": ("Хореограф", "постановщик танцев|choreographer"),
    "host": ("Ведущий", "ведущая|конферансье|mc|host"),
    "makeup": ("Гримёр", "гример|визажист|makeup artist|make-up artist"),
    "wardrobe": ("Костюмер", "костюмерша|wardrobe technician|wardrobe"),
}
ALIASES = {
    normalize(alias): key
    for key, (title, aliases) in ROLES.items()
    for alias in [title, key, *aliases.split("|")]
}
SOUND_ROLES = {"foh", "mon", "audio_tech", "system", "rf", "playback"}
AMBIGUOUS = {"фон", "tm", "тм", "техник", "инженер", "оператор"}


class RoleIndex:
    def __init__(self, definitions: list[tuple[str, str, list[str]]] | None = None):
        rows = (
            definitions
            if definitions is not None
            else [(key, title, aliases.split("|")) for key, (title, aliases) in ROLES.items()]
        )
        self.titles = {key: title for key, title, _ in rows}
        self.aliases = {
            normalize(alias): key
            for key, title, aliases in rows
            for alias in [key, title, *aliases]
        }

    def key(self, name: str) -> str:
        text = normalize(name)
        return self.aliases.get(text, "custom:" + text)

    def matches(self, required: str, offered: str) -> bool:
        target, skill = self.key(required), self.key(offered)
        return target == skill or target == "sound" and skill in SOUND_ROLES

    @staticmethod
    def _names(value: str) -> list[str]:
        names = [s.strip() for s in re.split(r"[,;\n]+", value) if s.strip()]
        require(
            0 < len(names) <= 40
            and all(len(s) <= 100 and not any(ord(c) < 32 for c in s) for s in names),
            "input",
        )
        return names

    def local_names(self, value: str) -> list[str]:
        # RU: Роль в коллективе сохраняет написание и не заявляет глобальные навыки.
        # EN: Local positions may be broad; they never imply specific professional skills.
        result: dict[str, str] = {}
        for name in self._names(value):
            result.setdefault(self.key(name), name)
        return list(result.values())

    def parse(self, value: str) -> list[str]:
        names = self._names(value)
        require(
            not any(normalize(s) in AMBIGUOUS or self.key(s) == "sound" for s in names),
            "profile_roles_ambiguous",
        )
        result = []
        for name in names:
            key = self.key(name)
            title = self.titles.get(key, name)
            if title not in result:
                result.append(title)
        return result


def role_key(name: str) -> str:
    return RoleIndex().key(name)


def matches(required: str, offered: str) -> bool:
    return RoleIndex().matches(required, offered)


def parse_skills(value: str) -> list[str]:
    return RoleIndex().parse(value)
