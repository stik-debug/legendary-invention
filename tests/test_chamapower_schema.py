from db import DB, init_db


def test_chamapower_schema_is_additive_and_empty(tmp_path):
    db = DB('sqlite:///' + str(tmp_path / 'power.db'))
    try:
        init_db(db)
        tables = {r['name'] for r in db.all("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'chama_power_projects', 'chama_power_votes', 'chama_power_quotes', 'chama_power_updates'} <= tables
        for table in ('chama_power_projects', 'chama_power_votes', 'chama_power_quotes', 'chama_power_updates'):
            assert db.val(f'SELECT COUNT(*) FROM {table}', default=0) == 0
        vote_cols = {r['name'] for r in db.all('PRAGMA table_info(chama_power_votes)')}
        assert {'project_id', 'chama_id', 'user_id', 'choice'} <= vote_cols
        quote_cols = {r['name'] for r in db.all('PRAGMA table_info(chama_power_quotes)')}
        assert {'supplier_name', 'amount_cents', 'evidence_reference', 'verification_status'} <= quote_cols
    finally:
        db.close()
