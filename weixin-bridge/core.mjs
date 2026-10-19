export function usable(state, now = Date.now()) {
  return Boolean(state.context && state.contextAt && now-state.contextAt<23*3600000 && (state.used||0)<9);
}
export function acceptMessages(state, response, userId, now = Date.now()) {
  for (const msg of response.msgs || []) {
    // Only the person who scanned the binding QR may activate this notification channel.
    if (msg.from_user_id !== userId || msg.message_type !== 1 || !msg.context_token) continue;
    if (!msg.create_time_ms || Number(msg.create_time_ms) < now-23*3600000 || Number(msg.create_time_ms)>now+300000) continue;
    if ((msg.message_id && String(msg.message_id)===state.lastMessageId) || (state.context===msg.context_token && Number(msg.create_time_ms)===state.contextAt)) continue;
    state.context=msg.context_token;state.contextAt=Number(msg.create_time_ms);state.used=0;
    state.lastMessageId=msg.message_id?String(msg.message_id):null;
  }
  if (response.get_updates_buf) state.cursor=response.get_updates_buf;
  return state;
}
