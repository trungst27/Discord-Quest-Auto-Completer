"""Private, bounded-rate status messages; never include credentials or exceptions."""
import asyncio
import time

import discord


class ProgressDM:
    def __init__(self, user):
        self.user = user
        self.message = None
        self.disabled = False
        self.started = time.monotonic()
        self.stage = 'Đang chờ lượt xử lý'

    async def update(self, text):
        if self.disabled or self.user is None:
            return
        try:
            if self.message is None:
                self.message = await self.user.send(text)
            else:
                await self.message.edit(content=text)
        except discord.HTTPException:
            self.disabled = True

    async def watch(self):
        while True:
            await asyncio.sleep(30)
            elapsed = int(time.monotonic() - self.started)
            await self.update(
                f'Quest • {self.stage}\nĐã chờ/chạy: {elapsed // 60} phút {elapsed % 60} giây.\n'
                'Kết quả sẽ cập nhật tại tin nhắn này.'
            )

    async def run(self, operation):
        self.started = time.monotonic()
        self.stage = 'Đang chờ lượt xử lý'
        await self.update('Quest • Đang chờ lượt xử lý…')
        watcher = asyncio.create_task(self.watch())
        try:
            result = await operation(self)
        except asyncio.CancelledError:
            await self.update('Quest • Đã yêu cầu dừng. Tác vụ HTTP đang chạy có thể cần thời gian kết thúc.')
            raise
        except Exception:
            await self.update('Quest • Chu kỳ gặp lỗi. Dùng /quest status để kiểm tra.')
            raise
        else:
            if result.get('error'):
                await self.update('Quest • Không lấy được kết quả. Dùng /quest status để kiểm tra.')
            else:
                await self.update(
                    f"Quest • Đã kết thúc chu kỳ.\n"
                    f"Tìm thấy: {result.get('total', 0)} quest.\n"
                    f"Chương trình báo hoàn thành trong lượt: {len(result.get('completed_now') or [])}."
                )
            return result
        finally:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
