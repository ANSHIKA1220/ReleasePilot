"""Run against a live API with the administrator credentials in the environment."""
import os
import uuid
from playwright.sync_api import sync_playwright, expect

with sync_playwright() as playwright:
    browser=playwright.chromium.launch()
    page=browser.new_page()
    page.goto(os.getenv("PILOT_URL","http://127.0.0.1:8000"))
    page.get_by_label("Email",exact=True).fill(os.environ["PILOT_ADMIN_EMAIL"])
    page.get_by_label("Password",exact=True).fill(os.environ["PILOT_ADMIN_PASSWORD"])
    page.get_by_role("button",name="Sign in").click()
    expect(page.get_by_role("heading",name="Releases",exact=True)).to_be_visible()
    page.get_by_role("button",name="Services",exact=True).click()
    page.get_by_text("Register service",exact=True).click()
    name="browser-"+uuid.uuid4().hex[:10]
    page.get_by_label("Service name").fill(name)
    page.get_by_label("GitHub repository").fill("demo/browser")
    page.get_by_label("Deployment name").fill(name)
    page.get_by_role("button",name="Register target").click()
    expect(page.get_by_role("heading",name=name)).to_be_visible()
    page.get_by_role("button",name="Audit",exact=True).click()
    expect(page.get_by_text("service.registered",exact=True).first).to_be_visible()
    page.get_by_role("button",name="Sign out").click()
    expect(page.get_by_role("button",name="Sign in")).to_be_visible()
    browser.close()
