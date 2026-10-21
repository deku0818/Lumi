// Dedicated native action: never grant arbitrary custom-protocol navigation to Markdown.
function codexThreadUrl(id) {
  if (typeof id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) {
    throw new Error('Invalid Codex thread ID')
  }
  return `codex://threads/${id}`
}
module.exports = { codexThreadUrl }
