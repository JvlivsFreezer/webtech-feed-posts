// Auth lives in HttpOnly cookies set by the server, so JavaScript never sees
// the token. The server tells us who is logged in via the #app-data JSON
// rendered into every page.

let appData = null;

function readAppData() {
  if (appData === null) {
    const el = document.getElementById("app-data");
    appData = el ? JSON.parse(el.textContent) : {};
  }
  return appData;
}

// Tokens used to live in localStorage - clear any left over from before
try {
  localStorage.removeItem("access_token");
} catch {
  // Storage unavailable
}

export async function getCurrentUser() {
  return readAppData().currentUser ?? null;
}

export function isLoggedIn() {
  return Boolean(readAppData().currentUser);
}

export async function logout() {
  try {
    await fetch("/api/users/logout", {
      method: "POST",
      headers: { "X-Requested-With": "fetch" },
    });
  } finally {
    window.location.href = "/";
  }
}
