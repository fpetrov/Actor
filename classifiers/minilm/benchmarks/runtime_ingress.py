"""Development probe using repository test fixtures; install runtime[dev]."""
import asyncio
import json
import statistics
import time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]/'runtime/tests'))
from actor_runtime.extensions import ExtensionRegistry
from actor_runtime.session import RuntimeSession
from actor_runtime.objectives import Objective
from actor_agents_arena import SOCIAL_ACTION_RESULT
from extensions.agents_arena.v1 import social_pb2 as pb
from conftest import manifest, negotiate
from test_social_runtime import observe, event

async def main():
    session=RuntimeSession(ExtensionRegistry.discover())
    await negotiate(session, manifest.__wrapped__())
    await observe(session)
    router=session.event_router
    await router.start(Objective(text='Complete assigned tasks.'))
    durations=[]
    for i in range(1200):
        start=time.perf_counter()
        await event(session, SOCIAL_ACTION_RESULT, pb.SocialActionResultEvent(match_id='match-social',
            correlation_id=f'operation-{i}', action=pb.SOCIAL_ACTION_WORK_TASK,accepted=True))
        while router._queue or router._in_flight_event:
            await asyncio.sleep(0)
        durations.append((time.perf_counter()-start)*1000)
    await router.close()
    session.close()
    values=sorted(durations[100:])
    print(json.dumps({'count':len(values),'p50_ms':statistics.median(values),'p95_ms':values[int(len(values)*.95)],
        'classified':router._counts['events_classified']}))
asyncio.run(main())
