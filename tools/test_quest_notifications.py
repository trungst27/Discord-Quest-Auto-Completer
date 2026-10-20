import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quest_notifications import ProgressDM


class NotificationsTest(unittest.IsolatedAsyncioTestCase):
    async def test_reuse_and_error_redaction(self):
        user = AsyncMock()
        reporter = ProgressDM(user)

        async def success(_):
            return {'total': 2, 'completed_now': ['one']}

        await reporter.run(success)
        await reporter.run(success)
        self.assertEqual(user.send.await_count, 1)
        self.assertIn('2 quest', user.send.return_value.edit.call_args.kwargs['content'])

        async def failed(_):
            raise ValueError('sensitive-test-value')

        with self.assertRaises(ValueError):
            await reporter.run(failed)
        self.assertNotIn('sensitive-test-value', user.send.return_value.edit.call_args.kwargs['content'])

    async def test_cancel_cleans_watcher(self):
        reporter = ProgressDM(AsyncMock())

        async def stopped(_):
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await reporter.run(stopped)
        self.assertEqual(
            [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()],
            [],
        )


if __name__ == '__main__':
    unittest.main()
