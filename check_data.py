import asyncio
import asyncpg
import os

async def check():
    conn = await asyncpg.connect(os.getenv('DATABASE_URL', 'postgresql://vibeforge:vibeforge_dev_secret@localhost:5432/vibeforge'))
    count = await conn.fetchval('SELECT COUNT(*) FROM gate_decisions')
    jobs = await conn.fetchval('SELECT COUNT(*) FROM jobs WHERE status = ', 'completed')
    escalations = await conn.fetchval('SELECT COUNT(*) FROM escalation_memory')
    print(f'Gate decisions: {count}')
    print(f'Completed jobs: {jobs}')
    print(f'Escalation memory records: {escalations}')
    await conn.close()

asyncio.run(check())
