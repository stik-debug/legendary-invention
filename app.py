    def ctx(chama_id, roles=None, allow_inactive=False):
        """Server-side gate for every chama route: must be an ACTIVE member of THIS chama, with the right role, and (unless allowed) a working subscription."""
        chama = db().one('SELECT * FROM chamas WHERE id=?', (chama_id,))
        if not chama:
            abort(404)

        m = db().one("SELECT * FROM chama_members WHERE chama_id=? AND user_id=? AND status='ACTIVE'", (chama_id, g.user['id']))
        if not m or (roles and m['role'] not in roles):
            abort(403)

        sub = S.sync_subscription(db(), chama_id)
        if not sub:
            abort(redirect(url_for('subscription', chama_id=chama_id)))
        if not allow_inactive and sub['status'] not in S.ACCESS_OK:
            abort(redirect(url_for('subscription', chama_id=chama_id)))

        return chama, m, sub
