/* UI-only lifecycle: no checkout state, PIN, or request belongs here. */
const sessions = new WeakMap()
const openDialogs = []

function focusableElements(root) {
  return [...root.querySelectorAll(
    'button, input, select, textarea, a[href], [tabindex]',
  )].filter((element) => !element.disabled && element.tabIndex >= 0
    && !element.closest('[inert]') && element.getClientRects().length > 0
    && getComputedStyle(element).visibility !== 'hidden')
}

function focusDialog(root) {
  root.querySelector('[role="dialog"]')?.focus({ preventScroll: true })
}

export default {
  mounted(root, binding) {
    const trigger = document.activeElement
    const background = []
    /* The overlays live inside existing components, not always under body. */
    for (let branch = root; branch.parentElement; branch = branch.parentElement) {
      for (const sibling of branch.parentElement.children) {
        if (sibling !== branch) {
          background.push([sibling, sibling.inert])
          sibling.inert = true
        }
      }
    }
    const session = { options: binding.value, trigger, background }
    const isTop = () => openDialogs.at(-1) === root
    session.keydown = (event) => {
      if (!isTop()) { return }
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        /* The caller retains its submitting/pending-close guards. */
        session.options?.onEscape?.()
      } else if (event.key === 'Tab') {
        const controls = focusableElements(root)
        const first = controls[0]
        const last = controls.at(-1)
        if (!first) {
          event.preventDefault()
          focusDialog(root)
        } else if (!root.contains(document.activeElement)
          || (event.shiftKey && (document.activeElement === first
            || document.activeElement === root.querySelector('[role="dialog"]')))
          || (!event.shiftKey && document.activeElement === last)) {
          event.preventDefault()
          ;(event.shiftKey ? last : first).focus()
        }
      }
    }
    session.focusin = (event) => {
      if (isTop() && !root.contains(event.target)) { focusDialog(root) }
    }
    sessions.set(root, session)
    openDialogs.push(root)
    document.addEventListener('keydown', session.keydown, true)
    document.addEventListener('focusin', session.focusin, true)
    focusDialog(root)
  },
  updated(root, binding) {
    const session = sessions.get(root)
    if (!session) { return }
    const changedStep = session.options?.step !== binding.value?.step
    session.options = binding.value
    if (changedStep && openDialogs.at(-1) === root) { focusDialog(root) }
  },
  beforeUnmount(root) {
    const session = sessions.get(root)
    if (!session) { return }
    document.removeEventListener('keydown', session.keydown, true)
    document.removeEventListener('focusin', session.focusin, true)
    openDialogs.splice(openDialogs.indexOf(root), 1)
    for (const [element, inert] of session.background) { element.inert = inert }
    sessions.delete(root)
    /* Startup registration has BODY as its trigger; a finished sale can also
       disable BAYAR. Neither is a usable return target, so use search. */
    const trigger = session.trigger
    const canRestore = trigger?.isConnected && !trigger.disabled && trigger.tabIndex >= 0
      && !trigger.closest('[inert]') && trigger.getClientRects().length > 0
      && getComputedStyle(trigger).visibility !== 'hidden'
    const target = canRestore ? trigger : document.querySelector('input[type="search"]')
    target?.focus({ preventScroll: true })
  },
}
