// 轻量 toast 提示
let _timer = null
export function showToast(msg, type = 'info') {
  document.querySelector('.toast-msg')?.remove()
  if (_timer) { clearTimeout(_timer); _timer = null }
  const d = document.createElement('div')
  d.className = 'toast-msg'
  const icon = type === 'success' ? 'check-circle' : type === 'error' ? 'times-circle' : 'info-circle'
  d.innerHTML = `<i class="fa fa-${icon}"></i> ${msg}`
  document.body.appendChild(d)
  _timer = setTimeout(() => d.remove(), 2500)
}
