// ═══════════════════════════════════════
// CLOTHYK — Shared JS (Premium Redesign)
// ═══════════════════════════════════════

// ─── SVG Icons ────────────────────────────────────────────────────────────
const Icons = {
  heart:    `<svg viewBox="0 0 24 24" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>`,
  heartFill:`<svg viewBox="0 0 24 24" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" style="fill:var(--gold);stroke:var(--gold)"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>`,
  bag:      `<svg viewBox="0 0 24 24" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>`,
  menu:     `<svg viewBox="0 0 24 24" stroke-width="1.5" stroke-linecap="round"><line x1="3" y1="7" x2="21" y2="7"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="17" x2="21" y2="17"/></svg>`,
  x:        `<svg viewBox="0 0 24 24" stroke-width="1.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
  arrow:    `<svg viewBox="0 0 24 24" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" fill="none"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>`,
  arrowLeft:`<svg viewBox="0 0 24 24" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" fill="none"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>`,
  check:    `<svg viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"><polyline points="20 6 9 17 4 12"/></svg>`,
  truck:    `<svg viewBox="0 0 24 24" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" fill="none"><rect x="1" y="3" width="15" height="13"/><path d="M16 8h4l3 3v5h-7V8z"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/></svg>`,
  whatsapp: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>`,
};

// ─── Toast ─────────────────────────────────────────────────────────────────
function showToast(message, type = "success") {
  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    document.body.appendChild(container);
  }
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => { toast.style.opacity = "0"; toast.style.transition = "opacity .4s"; }, 2600);
  setTimeout(() => toast.remove(), 3000);
}

// ─── API Helper ───────────────────────────────────────────────────────────
async function apiFetch(url, options = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Request failed" }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return await res.json();
}

// ─── Cart ─────────────────────────────────────────────────────────────────
const Cart = {
  get() { return JSON.parse(localStorage.getItem("clothyk_cart") || "[]"); },
  save(items) { localStorage.setItem("clothyk_cart", JSON.stringify(items)); updateCartBadge(); },
  add(product, size, color) {
    const items = this.get();
    const existing = items.find(i => i.id === product.id && i.size === size && i.color === color);
    if (existing) {
      existing.qty = (existing.qty || 1) + 1;
    } else {
      items.push({ id: product.id, name: product.name, price: product.our_price, image: product.image, size, color, qty: 1 });
    }
    this.save(items);
    showToast("Added to cart");
  },
  remove(id, size, color) {
    const items = this.get().filter(i => !(i.id === id && i.size === size && i.color === color));
    this.save(items);
  },
  count() { return this.get().reduce((s, i) => s + (i.qty || 1), 0); },
  total() { return this.get().reduce((s, i) => s + i.price * (i.qty || 1), 0); },
  clear() { localStorage.removeItem("clothyk_cart"); updateCartBadge(); }
};

// ─── Wishlist ─────────────────────────────────────────────────────────────
const Wishlist = {
  get() { return JSON.parse(localStorage.getItem("clothyk_wishlist") || "[]"); },
  save(items) { localStorage.setItem("clothyk_wishlist", JSON.stringify(items)); updateWishlistBadge(); },
  toggle(product) {
    const items = this.get();
    const idx = items.findIndex(i => i.id === product.id);
    if (idx > -1) {
      items.splice(idx, 1);
      showToast("Removed from wishlist", "warning");
    } else {
      items.push({ id: product.id, name: product.name, price: product.our_price, image: product.image, code: product.shopkeeper_code });
      showToast("Saved to wishlist");
    }
    this.save(items);
    return idx === -1;
  },
  has(id) { return this.get().some(i => i.id === id); },
  count() { return this.get().length; }
};

function updateCartBadge() {
  const badge = document.getElementById("cart-badge");
  if (badge) {
    const count = Cart.count();
    badge.textContent = count;
    badge.style.display = count > 0 ? "flex" : "none";
  }
}

function updateWishlistBadge() {
  const badge = document.getElementById("wishlist-badge");
  if (badge) {
    const count = Wishlist.count();
    badge.textContent = count;
    badge.style.display = count > 0 ? "flex" : "none";
  }
}

// ─── Skeleton ─────────────────────────────────────────────────────────────
function skeletonCards(container, count = 6) {
  container.innerHTML = Array(count).fill(`
    <div class="skeleton-card">
      <div class="skeleton skeleton-img"></div>
      <div style="padding:14px 16px">
        <div class="skeleton skeleton-text" style="width:65%;margin-bottom:8px"></div>
        <div class="skeleton skeleton-text" style="width:35%;height:11px"></div>
      </div>
    </div>
  `).join("");
}

// ─── Format ───────────────────────────────────────────────────────────────
function formatPrice(n) { return "₹" + Number(n).toLocaleString("en-IN"); }

// ─── Product Card ─────────────────────────────────────────────────────────
function renderProductCard(p) {
  const wishlisted = Wishlist.has(p.id);
  return `
    <div class="product-card" onclick="window.location='/product/${p.id}'">
      <div class="product-img-wrap">
        <img src="${p.image || '/static/images/placeholder.svg'}" alt="${p.name}" loading="lazy">
        <div class="product-card-overlay">
          <span class="btn btn-primary btn-sm" style="pointer-events:none">View Details</span>
        </div>
        <button class="wishlist-btn ${wishlisted ? 'active' : ''}"
          onclick="event.stopPropagation(); toggleWishlist(this, ${JSON.stringify(p).replace(/"/g,'&quot;')})"
          title="Save to wishlist">
          ${wishlisted ? Icons.heartFill : Icons.heart}
        </button>
      </div>
      <div class="product-card-body">
        ${p.category ? `<div class="product-card-category">${p.category}</div>` : ''}
        <div class="product-card-name">${p.name}</div>
        <div class="product-card-price">${formatPrice(p.our_price)}</div>
      </div>
    </div>
  `;
}

function toggleWishlist(btn, product) {
  const added = Wishlist.toggle(product);
  btn.innerHTML = added ? Icons.heartFill : Icons.heart;
  btn.classList.toggle("active", added);
}

// ─── Mobile Nav ───────────────────────────────────────────────────────────
function toggleMobileMenu() {
  const nav = document.getElementById("mobile-nav");
  const hamBtn = document.getElementById("hamburger-btn");
  if (!nav) return;
  const isOpen = nav.classList.toggle("open");
  if (hamBtn) hamBtn.innerHTML = isOpen ? Icons.x : Icons.menu;
}

// ─── Init ─────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  updateCartBadge();
  updateWishlistBadge();
});