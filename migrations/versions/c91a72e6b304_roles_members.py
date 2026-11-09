"""Editable professional vocabulary and durable membership board; preserve existing roles."""

from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "c91a72e6b304"
down_revision = "f23a91c8d604"
branch_labels = depends_on = None

SEEDS = [
    ("guitar", "Гитара", ["гитарист", "гитаристка", "guitar", "guitarist", "guitar player"]),
    (
        "bass",
        "Бас-гитара",
        ["бас", "басист", "басистка", "bass", "bassist", "bass player", "bass guitar"],
    ),
    ("drums", "Ударные", ["барабаны", "барабанщик", "ударник", "барабанщица", "drums", "drummer"]),
    ("percussion", "Перкуссия", ["перкуссионист", "percussion", "percussionist"]),
    (
        "keys",
        "Клавишные",
        [
            "клавиши",
            "клавишник",
            "клавишница",
            "пианист",
            "keyboard",
            "keys",
            "keyboard player",
            "pianist",
        ],
    ),
    (
        "vocals",
        "Вокал",
        [
            "вокалист",
            "вокалистка",
            "певец",
            "певица",
            "vocals",
            "singer",
            "vocalist",
            "lead vocals",
        ],
    ),
    (
        "backing_vocals",
        "Бэк-вокал",
        [
            "бэки",
            "бек-вокал",
            "бэк вокал",
            "бэк-вокалист",
            "бэк-вокалистка",
            "backing vocals",
            "backing singer",
            "bvox",
        ],
    ),
    ("dj", "Диджей", ["dj", "deejay", "диджейка"]),
    ("sax", "Саксофон", ["саксофонист", "sax", "saxophone", "saxophonist"]),
    ("trumpet", "Труба", ["трубач", "trumpet", "trumpeter"]),
    ("trombone", "Тромбон", ["тромбонист", "trombone", "trombonist"]),
    ("violin", "Скрипка", ["скрипач", "скрипачка", "violin", "violinist"]),
    ("cello", "Виолончель", ["виолончелист", "cello", "cellist"]),
    ("sound", "Звуковик", ["звук", "звукарь", "звукорежиссер", "sound engineer", "audio engineer"]),
    (
        "foh",
        "Звукорежиссёр FOH",
        [
            "foh",
            "фох",
            "foh engineer",
            "front of house",
            "front of house engineer",
            "звукорежиссер фох",
        ],
    ),
    (
        "mon",
        "Мониторный звукорежиссёр",
        [
            "мониторщик",
            "мон",
            "mon",
            "mon engineer",
            "monitor engineer",
            "мониторный звукорежиссер",
            "мониторный инженер",
        ],
    ),
    (
        "audio_tech",
        "Звуковой техник",
        ["техник по звуку", "audio technician", "sound tech", "audio tech"],
    ),
    (
        "system",
        "Системный инженер по звуку",
        ["системщик по звуку", "audio system engineer", "system engineer"],
    ),
    ("rf", "Радиотехник", ["техник радиосистем", "rf technician", "rf engineer", "радиоинженер"]),
    (
        "playback",
        "Инженер плейбэка",
        ["плейбэк", "плейбек", "плейбэк-инженер", "playback", "playback engineer"],
    ),
    (
        "backline",
        "Бэклайн-техник",
        ["бэклайн", "беклайн", "техник по инструментам", "backline", "backline technician"],
    ),
    ("guitar_tech", "Гитарный техник", ["гитарный тех", "guitar tech", "guitar technician"]),
    ("drum_tech", "Барабанный техник", ["барабанный тех", "drum tech", "drum technician"]),
    ("lighting", "Художник по свету", ["световик", "свет", "lighting designer", "ld"]),
    (
        "lighting_operator",
        "Оператор света",
        ["оператор светового пульта", "lighting operator", "light operator"],
    ),
    (
        "lighting_tech",
        "Световой техник",
        ["техник по свету", "lighting technician", "lighting tech"],
    ),
    ("video", "Видеоинженер", ["инженер по видео", "video engineer", "video technician"]),
    ("vj", "Виджей", ["vj", "видеооператор контента"]),
    ("tour", "Тур-менеджер", ["турик", "турменеджер", "тур менеджер", "tour manager"]),
    ("production", "Продакшн-менеджер", ["продакшн менеджер", "production manager"]),
    ("stage", "Стейдж-менеджер", ["стейдж", "менеджер сцены", "stage manager"]),
    ("director", "Концертный директор", ["директор коллектива", "concert director"]),
    ("administrator", "Администратор коллектива", ["администратор группы", "band administrator"]),
    ("rigger", "Риггер", ["такелажник", "rigger", "rigging technician"]),
    ("stagehand", "Рабочий сцены", ["сценический рабочий", "stagehand", "сценраб"]),
    ("dancer", "Танцор", ["танцовщик", "танцовщица", "танцорка", "dancer"]),
    ("choreographer", "Хореограф", ["постановщик танцев", "choreographer"]),
    ("host", "Ведущий", ["ведущая", "конферансье", "mc", "host"]),
    ("makeup", "Гримёр", ["гример", "визажист", "makeup artist", "make-up artist"]),
    ("wardrobe", "Костюмер", ["костюмерша", "wardrobe technician", "wardrobe"]),
]


def upgrade():
    op.add_column(
        "memberships", sa.Column("requested_roles", JSONB(none_as_null=True), nullable=True)
    )
    op.add_column(
        "memberships", sa.Column("version", sa.Integer(), nullable=False, server_default="0")
    )
    roles = op.create_table(
        "professional_roles",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("key", sa.String(100), nullable=False, unique=True),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("aliases", JSONB(), nullable=False),
        sa.Column("retired_aliases", JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version > 0", name="role_version"),
    )
    op.bulk_insert(
        roles,
        [
            {
                "id": uuid5(NAMESPACE_URL, "setlist-role:" + key),
                "key": key,
                "title": title,
                "aliases": aliases,
                "retired_aliases": [],
                "version": 1,
            }
            for key, title, aliases in SEEDS
        ],
    )
    op.create_table(
        "member_boards",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column(
            "group_id",
            UUID(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("group_epoch", sa.Integer(), nullable=False),
        sa.Column("message_ids", JSONB(), nullable=False),
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM member_boards) OR "
            "EXISTS(SELECT 1 FROM memberships WHERE requested_roles IS NOT NULL "
            "AND requested_roles <> 'null'::jsonb) OR "
            "EXISTS(SELECT 1 FROM professional_roles WHERE version > 1 OR left(key,5) = 'role_')"
        )
    ):
        raise RuntimeError("Downgrade would lose membership or vocabulary data; preserve a backup.")
    op.drop_table("member_boards")
    op.drop_table("professional_roles")
    op.drop_column("memberships", "version")
    op.drop_column("memberships", "requested_roles")
