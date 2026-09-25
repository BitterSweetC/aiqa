"""
Amazon add-to-cart — connects directly to the already-running Chrome on port 9222.
Run AFTER the setup script that launched Chrome with --remote-debugging-port=9222.
"""


from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright


def add_chair_to_amazon_cart():
    with sync_playwright() as p:
        print("🔌 Connecting to running Chrome on port 9222...")
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()

        try:
            print(f"📦 Current URL: {page.url}")
            if "amazon.com" not in page.url:
                page.goto("https://www.amazon.com", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2000)
            print(f"   Title: {page.title()}")

            # ── Search ────────────────────────────────────────────────────
            print("🔍 Searching for 'office chair'...")
            page.wait_for_selector("#twotabsearchtextbox", timeout=10000)
            page.fill("#twotabsearchtextbox", "office chair")
            page.keyboard.press("Enter")
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(3000)

            # ── Pick first product ─────────────────────────────────────────
            print("🪑 Clicking first product...")
            for sel in [
                "[data-component-type='s-search-result'] h2 a",
                "[data-asin]:not([data-asin='']) h2 a",
                "h2 a.a-link-normal",
            ]:
                items = page.locator(sel)
                if items.count() > 0:
                    name = (items.first.text_content() or "chair").strip()
                    print(f"   → {name[:70]}...")
                    items.first.click()
                    break

            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(3000)

            # ── Add to Cart ───────────────────────────────────────────────
            print("🛒 Adding to cart...")
            for sel in ["#add-to-cart-button", "input[name='submit.add-to-cart']",
                        "button:has-text('Add to Cart')"]:
                btn = page.locator(sel)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click()
                    page.wait_for_timeout(2500)
                    break

            # Dismiss upsell
            for sel in ["button:has-text('No thanks')", "button:has-text('Continue without')"]:
                try:
                    b = page.locator(sel)
                    if b.count() > 0 and b.first.is_visible():
                        b.first.click()
                        page.wait_for_timeout(1000)
                except Exception:  # noqa: BLE001, S110
                    pass

            # ── Result ────────────────────────────────────────────────────
            try:
                cart = page.locator("#nav-cart-count").text_content(timeout=4000).strip()
            except Exception:  # noqa: BLE001
                cart = "?"

            print(f"\n✅ Done! Cart now has {cart} item(s).")

        except PlaywrightTimeout as e:
            print(f"\n❌ Timeout: {e}")
            page.screenshot(path="amazon_error.png")
            print("   Screenshot: amazon_error.png")

        except Exception as e:  # noqa: BLE001
            print(f"\n❌ Error: {e}")
            try:
                page.screenshot(path="amazon_error.png")
                print("   Screenshot: amazon_error.png")
            except Exception:  # noqa: BLE001, S110
                pass

        finally:
            browser.close()
            print("Playwright disconnected (Chrome still running).")


if __name__ == "__main__":
    add_chair_to_amazon_cart()
