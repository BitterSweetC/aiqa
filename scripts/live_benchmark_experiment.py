"""Compare two local Playwright interaction techniques on one live page.

Both paths use Playwright and DOM-derived ground truth. No AIQA planner,
orchestrator, verifier, model API, image upload, or model inference runs here.
The record is useful for inspecting local browser component costs, but it is not
an end-to-end AIQA or multimodal-agent comparison.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from playwright.async_api import async_playwright


def write_json(path: Path, data: dict) -> None:
    """Serialize an experiment artifact outside browser operations."""
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


async def run_direct_playwright_dom(url: str, link_text: str, expected_url_substr: str) -> dict:
    """Run a direct Playwright locator interaction without the AIQA pipeline."""
    trace = []
    t_start = time.perf_counter()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Step 1: Navigation
        s1_start = time.perf_counter()
        await page.goto(url, wait_until="domcontentloaded")
        s1_end = time.perf_counter()
        trace.append(
            {
                "step": 1,
                "action": "navigate",
                "target": url,
                "latency_ms": round((s1_end - s1_start) * 1000, 2),
                "timestamp": time.time(),
            }
        )

        # Step 2: In-Memory DOM Extraction
        s2_start = time.perf_counter()
        # Direct in-memory DOM query; this does not call AIQA application code.
        elements = await page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('a, button, input, select'));
            return els.slice(0, 50).map(el => ({
                tag: el.tagName.toLowerCase(),
                text: el.innerText.trim(),
                href: el.href || null
            }));
        }""")
        s2_end = time.perf_counter()
        trace.append(
            {
                "step": 2,
                "action": "dom_extract",
                "elements_extracted": len(elements),
                "latency_ms": round((s2_end - s2_start) * 1000, 2),
                "timestamp": time.time(),
            }
        )

        # Step 3: Match & Native Click
        s3_start = time.perf_counter()
        locator = page.locator(f"a:has-text('{link_text}')").first
        await locator.click()
        await page.wait_for_load_state("domcontentloaded")
        s3_end = time.perf_counter()
        trace.append(
            {
                "step": 3,
                "action": "native_locator_click",
                "selector": f"a:has-text('{link_text}')",
                "latency_ms": round((s3_end - s3_start) * 1000, 2),
                "timestamp": time.time(),
            }
        )

        # Step 4: Verification
        s4_start = time.perf_counter()
        current_url = page.url
        passed = expected_url_substr in current_url
        s4_end = time.perf_counter()
        trace.append(
            {
                "step": 4,
                "action": "dom_url_assert",
                "expected": expected_url_substr,
                "actual": current_url,
                "passed": passed,
                "latency_ms": round((s4_end - s4_start) * 1000, 2),
                "timestamp": time.time(),
            }
        )

        await browser.close()

    total_latency_ms = round((time.perf_counter() - t_start) * 1000, 2)
    return {
        "paradigm": "Direct Playwright DOM locator",
        "total_latency_ms": total_latency_ms,
        "total_seconds": round(total_latency_ms / 1000, 3),
        "steps": trace,
        "payload_bytes": sum(len(json.dumps(e)) for e in elements)
        if "elements" in locals()
        else 512,
        "passed": passed,
    }


async def run_local_coordinate_simulation(
    url: str,
    link_text: str,
    expected_url_substr: str,
) -> dict:
    """Run a local screenshot and coordinate simulation without model inference."""
    trace = []
    t_start = time.perf_counter()
    total_payload_bytes = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()

        # Step 1: Navigation
        s1_start = time.perf_counter()
        await page.goto(url)
        s1_end = time.perf_counter()
        trace.append(
            {
                "step": 1,
                "action": "navigate",
                "target": url,
                "latency_ms": round((s1_end - s1_start) * 1000, 2),
                "timestamp": time.time(),
            }
        )

        # Step 2: Full Viewport Screenshot Capture
        s2_start = time.perf_counter()
        screenshot_bytes = await page.screenshot(type="png", full_page=False)
        s2_end = time.perf_counter()
        total_payload_bytes += len(screenshot_bytes)
        trace.append(
            {
                "step": 2,
                "action": "screenshot_rasterization",
                "size_bytes": len(screenshot_bytes),
                "size_kb": round(len(screenshot_bytes) / 1024, 1),
                "latency_ms": round((s2_end - s2_start) * 1000, 2),
                "timestamp": time.time(),
            }
        )

        # Step 3: Base64 Encoding & Network Serialization
        s3_start = time.perf_counter()
        b64_str = base64.b64encode(screenshot_bytes).decode("utf-8")
        s3_end = time.perf_counter()
        trace.append(
            {
                "step": 3,
                "action": "base64_payload_encode",
                "b64_chars": len(b64_str),
                "latency_ms": round((s3_end - s3_start) * 1000, 2),
                "timestamp": time.time(),
            }
        )

        # Step 4: Coordinate Resolution via Visual Bounding Box
        # We query the actual visual bounding box on screen to get ground-truth (x, y)
        s4_start = time.perf_counter()
        locator = page.locator(f"a:has-text('{link_text}')").first
        bbox = await locator.bounding_box()
        # DOM ground truth supplies coordinates; no visual model resolves the target.
        s4_end = time.perf_counter()
        trace.append(
            {
                "step": 4,
                "action": "bounding_box_resolution",
                "target_link": link_text,
                "box": bbox,
                "latency_ms": round((s4_end - s4_start) * 1000, 2),
                "timestamp": time.time(),
            }
        )

        # Step 5: Visual Mouse Move and Click
        s5_start = time.perf_counter()
        if bbox:
            click_x = bbox["x"] + bbox["width"] / 2
            click_y = bbox["y"] + bbox["height"] / 2
            await page.mouse.move(click_x, click_y)
            await page.mouse.click(click_x, click_y)
        s5_end = time.perf_counter()
        trace.append(
            {
                "step": 5,
                "action": "mouse_coordinate_click",
                "coordinates": {"x": round(click_x, 1), "y": round(click_y, 1)} if bbox else None,
                "latency_ms": round((s5_end - s5_start) * 1000, 2),
                "timestamp": time.time(),
            }
        )

        # Step 6: An explicit delay chosen by this simulation.
        s6_start = time.perf_counter()
        await asyncio.sleep(0.6)
        s6_end = time.perf_counter()
        trace.append(
            {
                "step": 6,
                "action": "visual_settle_delay",
                "description": "Fixed delay configured by this local simulation",
                "latency_ms": round((s6_end - s6_start) * 1000, 2),
                "timestamp": time.time(),
            }
        )

        # Step 7: Post-Action Verification Screenshot
        s7_start = time.perf_counter()
        verify_screenshot = await page.screenshot(type="png")
        total_payload_bytes += len(verify_screenshot)
        current_url = page.url
        passed = expected_url_substr in current_url
        s7_end = time.perf_counter()
        trace.append(
            {
                "step": 7,
                "action": "verification_screenshot_and_check",
                "size_kb": round(len(verify_screenshot) / 1024, 1),
                "passed": passed,
                "actual_url": current_url,
                "latency_ms": round((s7_end - s7_start) * 1000, 2),
                "timestamp": time.time(),
            }
        )

        await browser.close()

    total_latency_ms = round((time.perf_counter() - t_start) * 1000, 2)
    return {
        "paradigm": "Local Playwright coordinate simulation",
        "total_latency_ms": total_latency_ms,
        "total_seconds": round(total_latency_ms / 1000, 3),
        "steps": trace,
        "payload_bytes": total_payload_bytes,
        "payload_kb": round(total_payload_bytes / 1024, 1),
        "passed": passed,
    }


def build_experiment_record(
    *,
    target_url: str,
    link_text: str,
    expected_url_substr: str,
    direct_result: dict,
    coordinate_result: dict,
    timestamp: str | None = None,
) -> dict:
    """Build a self-describing record that states the experiment's limits."""
    return {
        "timestamp": timestamp or datetime.now(UTC).isoformat(),
        "experiment": {
            "kind": "local_playwright_component_comparison",
            "aiqa_pipeline_exercised": False,
            "model_inference_exercised": False,
            "comparison_scope": "local_browser_components_only",
            "limitations": [
                "Both paths use Playwright.",
                "DOM ground truth supplies the coordinate target.",
                "The fixed delay is a harness setting, not a measured model requirement.",
                "No model latency, token usage, cost, or reliability is measured.",
            ],
        },
        "target_url": target_url,
        "test_case": {
            "name": f"Navigate to '{link_text}' category",
            "action": f"Click link '{link_text}'",
            "expected_url_substr": expected_url_substr,
        },
        "results": {
            "direct_playwright_dom": direct_result,
            "local_coordinate_simulation": coordinate_result,
        },
    }


async def main():
    target_url = "https://books.toscrape.com/"
    link_to_click = "Books"
    expected_substr = "category/books_1/index.html"

    print("=" * 70)
    print(f"Running local Playwright component experiment on: {target_url}")
    print(f"Action: Click '{link_to_click}', Expect URL contains '{expected_substr}'")
    print("=" * 70)

    print("\n[1/2] Executing direct Playwright DOM locator...")
    direct_result = await run_direct_playwright_dom(target_url, link_to_click, expected_substr)
    print(
        "-> Direct Playwright DOM path finished in "
        f"{direct_result['total_latency_ms']} ms ({direct_result['total_seconds']} s)"
    )

    print("\n[2/2] Executing local screenshot and coordinate simulation...")
    coordinate_result = await run_local_coordinate_simulation(
        target_url,
        link_to_click,
        expected_substr,
    )
    print(
        "-> Local coordinate simulation finished in "
        f"{coordinate_result['total_latency_ms']} ms "
        f"({coordinate_result['total_seconds']} s)"
    )

    # Save to disk
    output_dir = Path("reports")
    output_dir.mkdir(exist_ok=True)
    report_file = output_dir / "live_benchmark_experiment_record.json"
    data = build_experiment_record(
        target_url=target_url,
        link_text=link_to_click,
        expected_url_substr=expected_substr,
        direct_result=direct_result,
        coordinate_result=coordinate_result,
    )
    write_json(report_file, data)

    print("\n" + "=" * 70)
    print("LOCAL PLAYWRIGHT COMPONENT EXPERIMENT COMPLETE")
    print(f"Experiment log saved to: {report_file}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
