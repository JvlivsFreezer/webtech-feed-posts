// Behaviour shared by every page: likes, bookmarks, follows, sharing, infinite
// scroll, relative timestamps, and the New Post / Edit Post form.
import { isLoggedIn, logout } from "/static/js/auth.js";
import {
  announce,
  apiFetch,
  initCharCounters,
  loginUrl,
  queueToast,
  readError,
  showQueuedToast,
  showToast,
  updateRelativeTimes,
} from "/static/js/utils.js";

const MAX_TAGS = 5;
const TAG_PATTERN = /^[a-z0-9][a-z0-9-]{0,29}$/;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const WORDS_PER_MINUTE = 200;

function networkError(error) {
  if (error?.name !== "AuthRedirectError") {
    showToast("Network error. Please check your connection and try again.", "error");
  }
}

// ---------------------------------------------------------------------------
// Like / bookmark / follow / share (event delegation, works for cards that
// infinite scroll adds later too)
// ---------------------------------------------------------------------------

function setPressed(button, pressed) {
  button.classList.toggle("active", pressed);
  button.setAttribute("aria-pressed", String(pressed));
}

async function toggleLike(button) {
  const postId = button.dataset.postId;
  const countEl = button.querySelector(".like-count");
  const wasLiked = button.getAttribute("aria-pressed") === "true";
  const previousCount = Number(countEl.textContent);

  // Optimistic update - feels instant, reverted if the request fails
  setPressed(button, !wasLiked);
  countEl.textContent = previousCount + (wasLiked ? -1 : 1);
  button.classList.add("pop");
  button.addEventListener("animationend", () => button.classList.remove("pop"), { once: true });

  try {
    const response = await apiFetch(`/api/posts/${postId}/like`, {
      method: wasLiked ? "DELETE" : "POST",
    });
    if (!response.ok) throw new Error(await readError(response));
    const data = await response.json();
    setPressed(button, data.liked);
    countEl.textContent = data.likes;
    announce(data.liked ? "Post liked" : "Like removed");
  } catch (error) {
    setPressed(button, wasLiked);
    countEl.textContent = previousCount;
    if (error.name !== "AuthRedirectError") {
      showToast(error.message || "Couldn't update like.", "error");
    }
  }
}

async function toggleBookmark(button) {
  const postId = button.dataset.postId;
  const wasBookmarked = button.getAttribute("aria-pressed") === "true";
  setPressed(button, !wasBookmarked);

  try {
    const response = await apiFetch(`/api/posts/${postId}/bookmark`, {
      method: wasBookmarked ? "DELETE" : "POST",
    });
    if (!response.ok) throw new Error(await readError(response));
    const data = await response.json();
    setPressed(button, data.bookmarked);
    showToast(data.bookmarked ? "Saved to your bookmarks." : "Removed from bookmarks.", "info");
  } catch (error) {
    setPressed(button, wasBookmarked);
    if (error.name !== "AuthRedirectError") {
      showToast(error.message || "Couldn't update bookmark.", "error");
    }
  }
}

async function toggleFollow(button) {
  const userId = button.dataset.userId;
  const wasFollowing = button.getAttribute("aria-pressed") === "true";
  button.disabled = true;

  try {
    const response = await apiFetch(`/api/users/${userId}/follow`, {
      method: wasFollowing ? "DELETE" : "POST",
    });
    if (!response.ok) throw new Error(await readError(response));
    const data = await response.json();
    setPressed(button, data.following);
    button.textContent = data.following ? "Following" : "Follow";
    button.classList.toggle("following", data.following);
    button.classList.toggle("btn-primary", !data.following);
    button.classList.toggle("btn-outline-primary", data.following);
    document.querySelectorAll("[data-followers-count]").forEach((el) => {
      el.textContent = data.followers_count;
    });
    announce(data.following ? "Now following" : "Unfollowed");
  } catch (error) {
    if (error.name !== "AuthRedirectError") {
      showToast(error.message || "Couldn't update follow.", "error");
    }
  } finally {
    button.disabled = false;
  }
}

async function sharePost(button) {
  const url = new URL(button.dataset.url, window.location.origin).href;
  try {
    await navigator.clipboard.writeText(url);
    showToast("Link copied to clipboard.", "info");
  } catch {
    window.prompt("Copy this link:", url);
  }
}

const NEEDS_LOGIN = new Set(["like", "bookmark", "follow"]);
const ACTIONS = {
  like: toggleLike,
  bookmark: toggleBookmark,
  follow: toggleFollow,
  share: sharePost,
  logout: () => logout(),
};

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  const handler = button && ACTIONS[button.dataset.action];
  if (!handler) return;

  event.preventDefault();
  if (NEEDS_LOGIN.has(button.dataset.action) && !isLoggedIn()) {
    window.location.href = loginUrl();
    return;
  }
  handler(button);
});

// Sort dropdowns submit their form on change
document.querySelectorAll("select[data-autosubmit]").forEach((select) => {
  select.addEventListener("change", () => select.form.submit());
});

// ---------------------------------------------------------------------------
// Infinite scroll: fetch server-rendered cards from /partials/posts
// ---------------------------------------------------------------------------

function setupInfiniteScroll() {
  const container = document.getElementById("postsContainer");
  if (!container) return;

  const loadMoreBtn = document.getElementById("loadMoreBtn");
  const skeletons = document.getElementById("postSkeletons");
  const sentinel = document.getElementById("scrollSentinel");
  const endOfList = document.getElementById("endOfList");

  let nextSkip = Number(container.dataset.nextSkip);
  let hasMore = container.dataset.hasMore === "true";
  let loading = false;
  let observer = null;

  async function loadMore() {
    if (loading || !hasMore) return;
    loading = true;
    skeletons.classList.remove("d-none");
    if (loadMoreBtn) {
      loadMoreBtn.disabled = true;
      loadMoreBtn.textContent = "Loading…";
    }

    try {
      const response = await fetch(
        `/partials/posts?${container.dataset.partialQuery}&skip=${nextSkip}`,
        { credentials: "same-origin" },
      );
      if (!response.ok) throw new Error("Failed to fetch posts");

      const before = container.childElementCount;
      container.insertAdjacentHTML("beforeend", await response.text());
      const added = container.childElementCount - before;
      updateRelativeTimes(container);

      hasMore = response.headers.get("X-Has-More") === "true";
      nextSkip = Number(response.headers.get("X-Next-Skip"));
      announce(`Loaded ${added} more post${added === 1 ? "" : "s"}`);

      if (!hasMore) {
        loadMoreBtn?.parentElement.remove();
        observer?.disconnect();
        endOfList?.classList.remove("d-none");
      } else if (loadMoreBtn) {
        loadMoreBtn.textContent = "Load more posts";
      }
    } catch (error) {
      console.error("Error loading posts:", error);
      // Stop auto-loading; the button stays as a manual retry
      observer?.disconnect();
      if (loadMoreBtn) loadMoreBtn.textContent = "Couldn't load posts - try again";
    } finally {
      loading = false;
      skeletons.classList.add("d-none");
      if (loadMoreBtn) loadMoreBtn.disabled = false;
    }
  }

  loadMoreBtn?.addEventListener("click", loadMore);

  if (hasMore && "IntersectionObserver" in window) {
    observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) loadMore();
      },
      { rootMargin: "400px 0px" },
    );
    observer.observe(sentinel);
  }
}

// ---------------------------------------------------------------------------
// Post form (New Post modal and Edit Post modal share this)
// ---------------------------------------------------------------------------

export function parseTags(value) {
  const tags = [];
  for (const raw of value.split(",")) {
    const tag = raw.trim().replace(/^#+/, "").toLowerCase().replace(/\s+/g, "-");
    if (tag && !tags.includes(tag)) tags.push(tag);
  }
  return tags;
}

function validateTags(tags) {
  const invalid = tags.find((tag) => !TAG_PATTERN.test(tag));
  if (invalid) return `“${invalid}” isn't a valid tag. Use letters, numbers and dashes.`;
  if (tags.length > MAX_TAGS) return `You can add up to ${MAX_TAGS} tags.`;
  return "";
}

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

export function initPostForm(form) {
  initCharCounters(form);

  const title = form.querySelector('[name="title"]');
  const content = form.querySelector('[name="content"]');
  const tagsInput = form.querySelector('[name="tags"]');
  const imageInput = form.querySelector('[name="image"]');
  const removeImage = form.querySelector('[name="remove_image"]');
  const preview = form.querySelector("[data-preview-for]");
  const stats = form.querySelector("[data-stats-for]");
  const tagsPreview = form.querySelector("[data-tags-preview-for]");
  const imagePreview = form.querySelector("[data-image-preview-for]");
  const originalImage = imagePreview.getAttribute("src");

  // Word count + reading time
  const updateStats = () => {
    const words = content.value.trim().split(/\s+/).filter(Boolean).length;
    const minutes = Math.max(1, Math.ceil(words / WORDS_PER_MINUTE));
    stats.textContent = `${words} word${words === 1 ? "" : "s"} · ~${minutes} min read`;
  };
  content.addEventListener("input", updateStats);
  updateStats();

  title.addEventListener("input", () => title.classList.remove("is-invalid"));
  content.addEventListener("input", () => content.classList.remove("is-invalid"));

  // Tag chips + inline validation
  const updateTags = () => {
    const tags = parseTags(tagsInput.value);
    const error = validateTags(tags);
    tagsInput.classList.toggle("is-invalid", Boolean(error));
    tagsInput.parentElement.querySelector(".invalid-feedback").textContent = error;
    tagsPreview.replaceChildren(
      ...tags.map((tag) => {
        const chip = document.createElement("span");
        chip.className = "tag-chip";
        chip.textContent = `#${tag}`;
        return chip;
      }),
    );
  };
  tagsInput.addEventListener("input", updateTags);
  updateTags();

  // Write / Preview tabs - the preview is rendered by the server so it matches
  // exactly what will be published
  const renderPreview = async () => {
    if (!content.value.trim()) {
      preview.innerHTML = '<p class="text-body-secondary">Nothing to preview yet.</p>';
      return;
    }
    preview.innerHTML = '<p class="text-body-secondary">Rendering preview…</p>';
    try {
      const response = await apiFetch("/api/posts/preview", {
        method: "POST",
        json: { content: content.value },
      });
      if (!response.ok) throw new Error(await readError(response));
      // Safe: the server sanitizes the rendered HTML
      preview.innerHTML = (await response.json()).html;
    } catch (error) {
      if (error.name !== "AuthRedirectError") {
        preview.innerHTML = '<p class="text-danger">Preview failed. Please try again.</p>';
      }
    }
  };
  const debouncedPreview = debounce(renderPreview, 400);

  form.querySelectorAll("[data-editor-tab]").forEach((tab) => {
    tab.addEventListener("click", () => {
      const showPreview = tab.dataset.editorTab === "preview";
      form.querySelectorAll("[data-editor-tab]").forEach((other) => {
        const active = other === tab;
        other.classList.toggle("active", active);
        other.setAttribute("aria-selected", String(active));
      });
      content.classList.toggle("d-none", showPreview);
      preview.classList.toggle("d-none", !showPreview);
      if (showPreview) renderPreview();
    });
  });
  content.addEventListener("input", () => {
    if (!preview.classList.contains("d-none")) debouncedPreview();
  });

  // Image preview
  imageInput.addEventListener("change", () => {
    const file = imageInput.files[0];
    imageInput.classList.remove("is-invalid");
    if (file) {
      if (removeImage) removeImage.checked = false;
      imagePreview.src = URL.createObjectURL(file);
      imagePreview.classList.remove("d-none");
    } else if (originalImage) {
      imagePreview.src = originalImage;
    } else {
      imagePreview.classList.add("d-none");
    }
  });
  removeImage?.addEventListener("change", () => {
    imagePreview.classList.toggle("d-none", removeImage.checked);
    if (removeImage.checked) imageInput.value = "";
  });
}

/**
 * Validate and send the post form, then upload/remove the image.
 * Returns the saved post, or null if validation or a request failed.
 */
export async function submitPostForm(form, { url, method, postId = null }) {
  const title = form.querySelector('[name="title"]');
  const content = form.querySelector('[name="content"]');
  const tagsInput = form.querySelector('[name="tags"]');
  const imageInput = form.querySelector('[name="image"]');
  const removeImage = form.querySelector('[name="remove_image"]');
  const submitButton = form.querySelector('button[type="submit"]');

  const tags = parseTags(tagsInput.value);
  const tagError = validateTags(tags);
  const file = imageInput.files[0];

  title.classList.toggle("is-invalid", !title.value.trim());
  content.classList.toggle("is-invalid", !content.value.trim());
  tagsInput.classList.toggle("is-invalid", Boolean(tagError));
  if (file && file.size > MAX_IMAGE_BYTES) {
    imageInput.classList.add("is-invalid");
    showToast("Image is too large. Maximum size is 5MB.", "error");
    return null;
  }
  const firstInvalid = form.querySelector(".is-invalid");
  if (firstInvalid) {
    // Make sure the content box is visible if that's what needs fixing
    if (firstInvalid === content) form.querySelector('[data-editor-tab="write"]').click();
    firstInvalid.focus();
    return null;
  }

  const originalText = submitButton.textContent;
  submitButton.disabled = true;
  submitButton.textContent = "Saving…";

  try {
    const response = await apiFetch(url, {
      method,
      json: { title: title.value.trim(), content: content.value, tags },
    });
    if (!response.ok) {
      showToast(await readError(response), "error");
      return null;
    }
    const post = await response.json();
    const id = postId ?? post.id;

    if (file) {
      const formData = new FormData();
      formData.append("file", file);
      const imageResponse = await apiFetch(`/api/posts/${id}/image`, {
        method: "PATCH",
        body: formData,
      });
      if (!imageResponse.ok) {
        // The post itself saved, so carry on but tell the user
        queueToast(`Post saved, but the image failed: ${await readError(imageResponse)}`, "error");
      }
    } else if (removeImage?.checked) {
      await apiFetch(`/api/posts/${id}/image`, { method: "DELETE" });
    }
    return post;
  } catch (error) {
    networkError(error);
    return null;
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = originalText;
  }
}

function setupCreatePostForm() {
  const form = document.getElementById("createPostForm");
  if (!form) return;
  initPostForm(form);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const post = await submitPostForm(form, { url: "/api/posts", method: "POST" });
    if (post) {
      queueToast(`“${post.title}” published!`);
      window.location.href = `/posts/${post.id}`;
    }
  });

  // Focus the title when the modal opens
  document.getElementById("createPostModal").addEventListener("shown.bs.modal", () => {
    form.querySelector('[name="title"]').focus();
  });
}

// ---------------------------------------------------------------------------

showQueuedToast();
updateRelativeTimes();
setInterval(updateRelativeTimes, 60_000);
setupInfiniteScroll();
setupCreatePostForm();
