from db import DB, init_db


def test_strategy_tables_and_vote_scenario_migration(tmp_path):
    path = tmp_path / 'strategy-test.db'
    db = DB('sqlite:///' + str(path))
    try:
        init_db(db)
        tables = {r['name'] for r in db.all("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'chama_scenarios', 'chama_constitution_history', 'chama_handovers', 'chama_financial_passports'} <= tables
        indexes = {r['name'] for r in db.all("SELECT name FROM sqlite_master WHERE type='index'")}
        assert 'ux_one_pending_handover_per_chama' in indexes
        cols = {r['name'] for r in db.all('PRAGMA table_info(chama_votes)')}
        assert 'scenario_id' in cols
        constitution_cols = {r['name'] for r in db.all('PRAGMA table_info(chama_constitutions)')}
        preference_cols = {r['name'] for r in db.all('PRAGMA table_info(notification_preferences)')}
        assert 'contribution_due_day' in constitution_cols
        assert 'whatsapp' in preference_cols

        # Simulate a prior schema and ensure the additive repair restores the new optional column.
        db.execute('ALTER TABLE chama_votes DROP COLUMN scenario_id')
        db.commit()
        init_db(db)
        cols = {r['name'] for r in db.all('PRAGMA table_info(chama_votes)')}
        assert 'scenario_id' in cols
        constitution_cols = {r['name'] for r in db.all('PRAGMA table_info(chama_constitutions)')}
        preference_cols = {r['name'] for r in db.all('PRAGMA table_info(notification_preferences)')}
        assert 'contribution_due_day' in constitution_cols
        assert 'whatsapp' in preference_cols
    finally:
        db.close()
