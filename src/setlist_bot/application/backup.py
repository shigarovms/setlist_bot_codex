import csv
import io
import json
from dataclasses import asdict
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.domain import entities as e


class Backup:
    """Export shared group data. Private drafts and Telegram identifiers stay private."""

    def __init__(self, store: Store, user: e.User) -> None:
        self.store, self.user = store, user

    async def build(self, group_id: UUID) -> bytes:
        group = await Groups(self.store, self.user).access(group_id, admin=True)
        data: dict[str, object] = {
            "format": "setlist-shared-data",
            "version": 1,
            "created_at": e.now().isoformat(),
            "private_drafts_included": False,
            "group": {
                "id": str(group.id),
                "name": group.name,
                "language": group.language,
                "default_pause_seconds": group.default_pause_seconds,
            },
        }
        for name, kind in (
            ("songs", e.Song),
            ("aliases", e.SongAlias),
            ("catalog_proposals", e.CatalogProposal),
            ("concerts", e.Concert),
            ("setlists", e.Setlist),
            ("blocks", e.Block),
            ("items", e.Item),
            ("revisions", e.Revision),
            ("submissions", e.Submission),
        ):
            data[name] = [asdict(row) for row in await self.store.find(kind, group_id=group_id)]
        members = []
        for member in await self.store.find(e.Membership, group_id=group_id):
            user = await self.store.get(e.User, member.user_id)
            members.append(
                {
                    "member_key": str(user.id),
                    "display_name": user.display_name,
                    "role": member.role,
                    "musical_role": member.musical_role,
                    "status": member.status,
                }
            )
        data["members"] = members
        output = io.BytesIO()
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr(
                "data.json", json.dumps(data, ensure_ascii=False, default=str, indent=2)
            )
            for name, rows in data.items():
                if not isinstance(rows, list) or not rows:
                    continue
                table = io.StringIO(newline="")
                writer = csv.DictWriter(table, fieldnames=list(rows[0]))
                writer.writeheader()
                for row in rows:
                    cells = {}
                    for key, value in row.items():
                        value = (
                            json.dumps(value, ensure_ascii=False, default=str)
                            if isinstance(value, (dict, list))
                            else str(value if value is not None else "")
                        )
                        # Spreadsheet viewers must not execute user-supplied formulae.
                        cells[key] = (
                            "'" + value
                            if value.startswith(("=", "+", "-", "@", "\t", "\r"))
                            else value
                        )
                    writer.writerow(cells)
                archive.writestr(name + ".csv", "\ufeff" + table.getvalue())
            archive.writestr(
                "README.txt",
                (
                    "Общие данные коллектива. Формат setlist-shared-data, версия 1.\n"
                    "data.json содержит связи по UUID; CSV предназначены для просмотра.\n"
                    "Личные черновики, Telegram ID, токены и технические сессии не включены.\n"
                    "Из корзины коллектив восстанавливается командой /trash в течение 30 дней.\n"
                    "Автоматический обратный импорт ZIP пока не реализован.\n"
                ),
            )
        return output.getvalue()
