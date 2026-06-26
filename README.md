# 👗 Clothyk — Indian Fashion Marketplace

A commission-based dress e-commerce store. Shopkeepers send products → you list them → customers order via WhatsApp → you pocket the margin.

---

## 🏗️ Tech Stack

| Layer | Tech |
|-------|------|
| Backend | Python FastAPI |
| Database | Supabase (PostgreSQL) |
| Cache | Redis |
| Frontend | HTML + CSS + Vanilla JS |
| Templates | Jinja2 |
| WhatsApp | UltraMsg API |
| QR Code | qrcode + Pillow |
| Auth | JWT (httponly cookies) + bcrypt |
| Rate Limiting | slowapi |
| Captcha | Cloudflare Turnstile |
| Error Monitoring | Sentry |
| Hosting | Railway |
| CDN + Security | Cloudflare |
| Uptime | UptimeRobot |

---

## 📁 Project Structure

```
clothyk/
├── main.py                  # FastAPI app, middleware, startup
├── requirements.txt
├── Procfile                 # Railway start command
├── railway.json
├── schema.sql               # Run once in Supabase SQL Editor
├── .env.example
├── routers/
│   ├── auth.py              # Admin login/logout
│   ├── products.py          # Product CRUD + public API
│   ├── orders.py            # Order creation + admin management
│   ├── shopkeepers.py       # Shopkeeper CRUD
│   ├── admin.py             # Dashboard data
│   ├── analytics.py         # Full analytics endpoint
│   ├── whatsapp.py          # WhatsApp agent webhook
│   └── public.py            # Page routing
├── utils/
│   ├── db.py                # Supabase client
│   ├── cache.py             # Redis async cache
│   ├── auth_utils.py        # JWT, bcrypt helpers
│   ├── whatsapp_utils.py    # UltraMsg + QR + message templates
│   └── captcha.py           # Cloudflare Turnstile verifier
├── templates/
│   ├── customer/
│   │   ├── home.html
│   │   ├── products.html
│   │   ├── product_detail.html
│   │   ├── cart.html
│   │   ├── wishlist.html
│   │   └── checkout.html
│   └── admin/
│       ├── _sidebar.html
│       ├── login.html
│       ├── dashboard.html
│       ├── products.html
│       ├── shopkeepers.html
│       ├── orders.html
│       └── analytics.html
└── static/
    ├── css/
    │   ├── shared.css
    │   └── admin.css
    ├── js/
    │   └── shared.js
    └── images/
        └── placeholder.svg
```

---

## 🚀 Setup Guide

### 1. Supabase Setup

1. Create project at [supabase.com](https://supabase.com)
2. Go to **SQL Editor** → paste and run `schema.sql`
3. Go to **Storage** → New Bucket → name: `product-images`, Public: ✅
4. Copy your **Project URL** and **service_role key** from Settings → API

### 2. Environment Variables

Copy `.env.example` to `.env` and fill in all values:

```bash
cp .env.example .env
```

Key variables:
- `SUPABASE_URL` + `SUPABASE_KEY` (service_role, not anon!)
- `REDIS_URL` — from Railway Redis plugin
- `SECRET_KEY` — generate with `python -c "import secrets; print(secrets.token_hex(32))"`
- `WHATSAPP_NUMBER` — your business WhatsApp (91XXXXXXXXXX)
- `ULTRAMSG_INSTANCE` + `ULTRAMSG_TOKEN` — from [ultramsg.com](https://ultramsg.com)
- `CLOUDFLARE_TURNSTILE_SECRET` + site key — from Cloudflare dashboard
- `UPI_ID` — your UPI ID (e.g. business@paytm)

### 3. Local Development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run
uvicorn main:app --reload --port 8000
```

Visit:
- Store: http://localhost:8000
- Admin: http://localhost:8000/admin/login (admin / admin123)

**Change the admin password immediately after first login!**

### 4. Cloudflare Turnstile

1. Go to Cloudflare dashboard → Turnstile
2. Add site → copy **Site Key** (public) and **Secret Key** (private)
3. In `templates/customer/checkout.html`, replace `YOUR_TURNSTILE_SITE_KEY` with your site key
4. Set `CLOUDFLARE_TURNSTILE_SECRET` env var with your secret key

### 5. UltraMsg WhatsApp Setup

1. Create account at [ultramsg.com](https://ultramsg.com)
2. Create instance → scan QR code with your WhatsApp Business number
3. Set webhook URL to: `https://yourdomain.com/api/whatsapp/webhook`
4. Copy Instance ID and Token to env vars

### 6. Deploy to Railway

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Create project
railway init

# Add Redis plugin in Railway dashboard

# Set env vars in Railway dashboard (Settings → Variables)

# Deploy
railway up
```

Or connect your GitHub repo to Railway for auto-deploy on push.

### 7. Cloudflare Setup

1. Add your Railway domain to Cloudflare
2. Enable **Polish** for image compression (Pro plan)
3. Enable **DDoS protection** (automatic)
4. Set SSL/TLS to Full (strict)

### 8. UptimeRobot

1. Create account at [uptimerobot.com](https://uptimerobot.com)
2. Add HTTP monitor → your Railway URL
3. Set interval: 5 minutes
4. This prevents Railway free tier from sleeping

---

## 🔑 Admin Panel

- Login: `/admin/login`
- Dashboard: `/admin/dashboard`
- Products: `/admin/products`
- Shopkeepers: `/admin/shopkeepers`
- Orders: `/admin/orders`
- Analytics: `/admin/analytics`

**Default credentials:** `admin` / `admin123`
→ Change via `/api/admin/change-password` after first login.

---

## 💬 WhatsApp Agent Commands

Customers can send to your WhatsApp number:
- `TRACK` — Check order status
- `CANCEL` — Cancel order
- `REVIEW` — Rate experience
- `HELP` — Show menu

---

## 🔒 Security Notes

- `shopkeeper_price` is **never** exposed in any public API response
- Shopkeeper name/contact is **never** sent to customers
- Admin sessions use httponly cookies (not localStorage)
- All secrets in Railway env vars, never hardcoded
- Cloudflare sits in front of Railway (real IPs via CF-Connecting-IP header)
- Rate limiting: checkout 5/min, admin login 3 attempts then IP block
- Supabase RLS: public can only READ products, all writes via service role

---

## 📊 Business Flow

```
Shopkeeper sends product photos/price via WhatsApp
          ↓
Admin adds product at higher price in admin panel
          ↓
Customer finds product on Clothyk website
          ↓
Customer places order via WhatsApp
          ↓
WhatsApp agent asks for payment choice (UPI/COD)
          ↓
UPI: Agent sends dynamic QR code → customer pays → admin verifies
COD: Auto-confirmed
          ↓
Admin contacts shopkeeper privately (never revealed to customer)
Gives: customer address + shopkeeper's agreed price
          ↓
Shopkeeper ships directly to customer
          ↓
Admin marks as Shipped in panel → Agent notifies customer
          ↓
Customer confirms delivery → Agent asks for review
          ↓
Profit = Our Price − Shopkeeper Price 💰
```

---

## 💰 Cost

| Service | Cost |
|---------|------|
| Railway | Free tier |
| Supabase | Free tier |
| Redis (Railway) | Free tier |
| Cloudflare | Free tier |
| Sentry | Free tier |
| UptimeRobot | Free tier |
| UltraMsg | ~₹800/month at scale |
| **Total** | **₹0/month to start** |
