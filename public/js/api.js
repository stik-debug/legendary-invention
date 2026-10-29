const API = {
  get token() { return localStorage.getItem('cp_token'); },
  set token(v) { v ? localStorage.setItem('cp_token', v) : localStorage.removeItem('cp_token'); },
  get user() { try { return JSON.parse(localStorage.getItem('cp_user') || 'null'); } catch { return null; } },
  set user(v) { v ? localStorage.setItem('cp_user', JSON.stringify(v)) : localStorage.removeItem('cp_user'); },
  get chamaId() { return localStorage.getItem('cp_chama'); },
  set chamaId(v) { v ? localStorage.setItem('cp_chama', v) : localStorage.removeItem('cp_chama'); },

  async request(path, options = {}) {
    const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    const res = await fetch(path, { ...options, headers });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.error || res.statusText || 'Request failed');
      err.code = data.code; err.status = res.status; throw err;
    }
    return data;
  },
  register(body) { return this.request('/api/auth/register', { method: 'POST', body: JSON.stringify(body) }); },
  login(body) { return this.request('/api/auth/login', { method: 'POST', body: JSON.stringify(body) }); },
  me() { return this.request('/api/auth/me'); },
  plans() { return this.request('/api/plans'); },
  createChama(body) { return this.request('/api/chamas', { method: 'POST', body: JSON.stringify(body) }); },
  getChama(id) { return this.request(`/api/chamas/${id}`); },
  members(id) { return this.request(`/api/chamas/${id}/members`); },
  addMember(id, body) { return this.request(`/api/chamas/${id}/members`, { method: 'POST', body: JSON.stringify(body) }); },
  removeMember(id, mid) { return this.request(`/api/chamas/${id}/members/${mid}`, { method: 'DELETE' }); },
  summary(id) { return this.request(`/api/chamas/${id}/summary`); },
  contributions(id) { return this.request(`/api/chamas/${id}/contributions`); },
  addContribution(id, body) { return this.request(`/api/chamas/${id}/contributions`, { method: 'POST', body: JSON.stringify(body) }); },
  ledger(id) { return this.request(`/api/chamas/${id}/ledger`); },
  loans(id) { return this.request(`/api/chamas/${id}/loans`); },
  applyLoan(id, body) { return this.request(`/api/chamas/${id}/loans`, { method: 'POST', body: JSON.stringify(body) }); },
  approveLoan(id, loanId, approve = true) {
    return this.request(`/api/chamas/${id}/loans/${loanId}/approve`, { method: 'POST', body: JSON.stringify({ approve }) });
  },
  disburseLoan(id, loanId) { return this.request(`/api/chamas/${id}/loans/${loanId}/disburse`, { method: 'POST', body: '{}' }); },
  repayLoan(id, loanId, body) { return this.request(`/api/chamas/${id}/loans/${loanId}/repay`, { method: 'POST', body: JSON.stringify(body) }); },
  fines(id) { return this.request(`/api/chamas/${id}/fines`); },
  addFine(id, body) { return this.request(`/api/chamas/${id}/fines`, { method: 'POST', body: JSON.stringify(body) }); },
  payFine(id, fineId, amountKes) {
    return this.request(`/api/chamas/${id}/fines/${fineId}/pay`, { method: 'POST', body: JSON.stringify({ amountKes }) });
  },
  meetings(id) { return this.request(`/api/chamas/${id}/meetings`); },
  addMeeting(id, body) { return this.request(`/api/chamas/${id}/meetings`, { method: 'POST', body: JSON.stringify(body) }); },
  messages(id) { return this.request(`/api/chamas/${id}/messages`); },
  sendMessage(id, body) { return this.request(`/api/chamas/${id}/messages`, { method: 'POST', body: JSON.stringify({ body }) }); },
  pay(id, phone) { return this.request(`/api/chamas/${id}/pay`, { method: 'POST', body: JSON.stringify({ phone }) }); },
  paymentCallback(body) { return this.request('/api/payments/callback', { method: 'POST', body: JSON.stringify(body) }); },
  verifyPayment(paymentId) { return this.request('/api/payments/verify/' + paymentId, { method: 'POST', body: '{}' }); },
  adminStats() { return this.request('/api/admin/stats'); },
  adminChamas(q) { return this.request('/api/admin/chamas' + (q ? '?q=' + encodeURIComponent(q) : '')); },
  adminSuspend(id, reason) { return this.request(`/api/admin/chamas/${id}/suspend`, { method: 'POST', body: JSON.stringify({ reason }) }); },
  adminReactivate(id) { return this.request(`/api/admin/chamas/${id}/reactivate`, { method: 'POST', body: '{}' }); },
  adminExtend(id, days) { return this.request(`/api/admin/chamas/${id}/extend`, { method: 'POST', body: JSON.stringify({ days }) }); },
  adminManualPay(id, body) { return this.request(`/api/admin/chamas/${id}/manual-payment`, { method: 'POST', body: JSON.stringify(body) }); },
  logout() { this.token = null; this.user = null; this.chamaId = null; location.href = '/login.html'; },
  requireAuth() {
    if (!this.token) { location.href = '/login.html?next=' + encodeURIComponent(location.pathname); return false; }
    return true;
  }
};
window.API = API;
