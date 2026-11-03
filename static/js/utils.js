// Error message extraction from API responses
export function getErrorMessage(error) {
  if (typeof error.detail === "string") {
    return error.detail;
  } else if (Array.isArray(error.detail)) {
    return error.detail
      .map((err) => err.msg.replace(/^Value error, /, ""))
      .join(". ");
  }
  return "An error occurred. Please try again.";
}

// Read the error message from a failed fetch Response
export async function readError(response) {
  if (response.status === 429) {
    return "Too many attempts. Please wait a bit and try again.";
  }
  try {
    return getErrorMessage(await response.json());
  } catch {
    return `Request failed (${response.status}). Please try again.`;
  }
}

export class AuthRedirectError extends Error {
  name = "AuthRedirectError";
}

export function loginUrl() {
  const next = window.location.pathname + window.location.search;
  return `/login?next=${encodeURIComponent(next)}`;
}

/**
 * fetch() for our API. Auth is an HttpOnly cookie, so there's no token to add,
 * but the server requires the X-Requested-With header on writes (CSRF check).
 * Pass `json` to send a JSON body. A 401 redirects to the login page unless
 * `allowUnauthorized` is set.
 */
export async function apiFetch(url, { json, allowUnauthorized = false, ...options } = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-Requested-With", "fetch");
  if (json !== undefined) {
    headers.set("Content-Type", "application/json");
    options.body = JSON.stringify(json);
  }

  const response = await fetch(url, {
    credentials: "same-origin",
    ...options,
    headers,
  });

  if (response.status === 401 && !allowUnauthorized) {
    window.location.href = loginUrl();
    throw new AuthRedirectError("Not logged in");
  }
  return response;
}

// Show a Bootstrap modal by ID
export function showModal(modalId) {
  const modal = bootstrap.Modal.getOrCreateInstance(
    document.getElementById(modalId),
  );
  modal.show();
  return modal;
}

// Hide a Bootstrap modal by ID
export function hideModal(modalId) {
  const modal = bootstrap.Modal.getInstance(document.getElementById(modalId));
  if (modal) modal.hide();
}

// Non-blocking notification in the corner. type: "success" | "error" | "info"
export function showToast(message, type = "success") {
  const container = document.getElementById("toastContainer");
  if (!container) return;

  const variant = { success: "success", error: "danger", info: "primary" }[type] || "secondary";
  const toast = document.createElement("div");
  toast.className = `toast align-items-center text-bg-${variant} border-0`;
  toast.setAttribute("role", type === "error" ? "alert" : "status");
  toast.setAttribute("aria-live", type === "error" ? "assertive" : "polite");
  toast.setAttribute("aria-atomic", "true");
  toast.innerHTML = `
    <div class="d-flex">
      <div class="toast-body"></div>
      <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
    </div>`;
  toast.querySelector(".toast-body").textContent = message;
  container.appendChild(toast);

  const instance = bootstrap.Toast.getOrCreateInstance(toast, {
    delay: type === "error" ? 6000 : 3500,
  });
  toast.addEventListener("hidden.bs.toast", () => toast.remove());
  instance.show();
}

// Show a toast on the next page load (for actions that navigate or reload)
export function queueToast(message, type = "success") {
  try {
    sessionStorage.setItem("queuedToast", JSON.stringify({ message, type }));
  } catch {
    // Storage unavailable (private mode etc.) - the toast is just skipped
  }
}

export function showQueuedToast() {
  try {
    const queued = sessionStorage.getItem("queuedToast");
    if (!queued) return;
    sessionStorage.removeItem("queuedToast");
    const { message, type } = JSON.parse(queued);
    showToast(message, type);
  } catch {
    // Ignore unreadable storage
  }
}

// Announce a change to screen readers
export function announce(message) {
  const region = document.getElementById("liveRegion");
  if (!region) return;
  region.textContent = "";
  // Clearing first makes repeated identical messages get announced
  requestAnimationFrame(() => {
    region.textContent = message;
  });
}

// Promise-based confirm dialog using the shared #confirmModal
export function confirmDialog({ title = "Are you sure?", message = "", confirmText = "Confirm" } = {}) {
  const modalEl = document.getElementById("confirmModal");
  modalEl.querySelector("#confirmModalLabel").textContent = title;
  modalEl.querySelector("#confirmModalMessage").textContent = message;
  const confirmButton = modalEl.querySelector("#confirmModalButton");
  confirmButton.textContent = confirmText;

  return new Promise((resolve) => {
    let confirmed = false;
    const onConfirm = () => {
      confirmed = true;
      bootstrap.Modal.getInstance(modalEl).hide();
    };
    confirmButton.addEventListener("click", onConfirm, { once: true });
    modalEl.addEventListener(
      "hidden.bs.modal",
      () => {
        confirmButton.removeEventListener("click", onConfirm);
        resolve(confirmed);
      },
      { once: true },
    );
    bootstrap.Modal.getOrCreateInstance(modalEl).show();
  });
}

// XSS prevention for dynamic content insertion
export function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// Date formatting to match server's strftime("%B %d, %Y")
export function formatDate(dateString) {
  const date = new Date(dateString);
  return date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "2-digit",
  });
}

const relativeFormatter = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const RELATIVE_UNITS = [
  ["year", 60 * 60 * 24 * 365],
  ["month", 60 * 60 * 24 * 30],
  ["week", 60 * 60 * 24 * 7],
  ["day", 60 * 60 * 24],
  ["hour", 60 * 60],
  ["minute", 60],
];

// "3 hours ago", "yesterday", "2 weeks ago"
export function relativeTime(dateString) {
  const seconds = (new Date(dateString).getTime() - Date.now()) / 1000;
  for (const [unit, unitSeconds] of RELATIVE_UNITS) {
    if (Math.abs(seconds) >= unitSeconds) {
      return relativeFormatter.format(Math.round(seconds / unitSeconds), unit);
    }
  }
  return "just now";
}

// Upgrade server-rendered <time data-relative> elements to relative times,
// keeping the exact local date/time as a hover tooltip
export function updateRelativeTimes(root = document) {
  root.querySelectorAll("time[data-relative]").forEach((el) => {
    const iso = el.getAttribute("datetime");
    el.textContent = relativeTime(iso);
    el.title = new Date(iso).toLocaleString(undefined, {
      dateStyle: "long",
      timeStyle: "short",
    });
  });
}

// Live "12/100" counters for elements with data-counter-for="<input id>"
export function initCharCounters(root = document) {
  root.querySelectorAll("[data-counter-for]").forEach((counter) => {
    const input = document.getElementById(counter.dataset.counterFor);
    if (!input) return;
    const max = Number(input.getAttribute("maxlength"));
    const update = () => {
      const length = input.value.length;
      counter.textContent = `${length}/${max}`;
      counter.classList.toggle("text-danger", length >= max * 0.9);
      counter.classList.toggle("text-body-secondary", length < max * 0.9);
    };
    input.addEventListener("input", update);
    update();
  });
}
