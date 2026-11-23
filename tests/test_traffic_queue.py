import threading
from pathlib import Path
from traffic_service.store import Store

def test_one_active_run_and_expired_lease_cannot_complete(tmp_path):
    s=Store(tmp_path/'state.db');ids=[]
    def create():ids.append(s.create_run([{'id':'tile'}]))
    threads=[threading.Thread(target=create) for _ in range(3)]
    for t in threads:t.start()
    for t in threads:t.join()
    assert sum(i is not None for i in ids)==1
    run=next(i for i in ids if i)
    token=s.claim(run,'tile',tmp_path/'capture')
    assert not s.transition(run,'tile','accepted',lease='wrong')
    with s.db(True) as db:db.execute('UPDATE jobs SET lease_until=0.1')
    assert not s.transition(run,'tile','accepted',lease=token)

def test_restart_reuses_complete_pair_and_cancellation_is_durable(tmp_path):
    s=Store(tmp_path/'state.db');run=s.create_run([{'id':'one'},{'id':'two'}])
    d=tmp_path/'one';d.mkdir();(d/'capture.json').write_text('{}');(d/'traffic-off.png').touch();(d/'traffic-on.png').touch()
    s.claim(run,'one',d);s.claim(run,'two',tmp_path/'two')
    s.cancel();restarted=Store(tmp_path/'state.db');restarted.recover()
    states={j['tile_id']:j['state'] for j in restarted.jobs(run)}
    assert states=={'one':'captured','two':'queued'}
    assert restarted.cancelled(run)
    restarted.finish(run,'cancelled')
    assert restarted.active() is None
    assert restarted.create_run([{'id':'new'}])
