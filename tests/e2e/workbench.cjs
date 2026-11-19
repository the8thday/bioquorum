/* Offline browser contract for the packaged workbench. Run with node. */
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const { chromium } = require("playwright");

async function serverURL(process) {
  let buffer = "";
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error("Workbench did not start")), 15000);
    process.stdout.on("data", (chunk) => {
      buffer += chunk;
      const found = buffer.match(/WORKBENCH_URL=(http:\/\/127\.0\.0\.1:\d+\/)/);
      if (found) {
        clearTimeout(timer);
        resolve(found[1]);
      }
    });
    process.once("exit", (code) => {
      clearTimeout(timer);
      reject(Error("Workbench exited with status " + code));
    });
  });
}

async function verifyProjectNote(page) {
  const first = await page.context().newPage();
  await first.goto(page.url());
  const editors = [page, first];
  for (const tab of editors) {
    await tab.getByRole("button", {name: "Project note", exact: true}).click();
    await tab.getByRole("dialog", {name: "Project note"}).waitFor();
  }
  const accepted = "Separate biological and technical replicates.";
  await page.getByRole("textbox", {name: "Note", exact: true}).fill(accepted);
  await page.getByRole("button", {name: "Save project note"}).click();
  await page.getByText("Project note saved.").waitFor();
  await first.getByRole("textbox", {name: "Note", exact: true}).fill("Stale edit");
  await first.getByRole("button", {name: "Save project note"}).click();
  await first.getByText(/The project note changed/).waitFor();
  await first.getByRole("button", {name: "Reload saved note"}).click();
  await first.waitForFunction((text) => document.getElementById("note-body").value === text, accepted);
  await first.close();
  await page.getByRole("button", {name: "Newer research", exact: true}).click();
  await page.getByRole("heading", {name: "Newer research", exact: true}).waitFor();
  await page.getByRole("button", {name: "Project note", exact: true}).click();
  assert.equal(await page.getByRole("textbox", {name: "Note", exact: true}).inputValue(), accepted);
  console.log("Project note shared across sessions; stale edit rejected");
}

async function verifyRealProject(page) {
  await page.getByRole("heading", {name: "Verify a cell-count calculation"}).waitFor();
  await page.getByRole("button", {name: "Rerun calculation"}).click();
  await page.getByText("Verification complete. Open the comparison under the claim.").waitFor();
  const last = page.getByText("Rerun · matched", {exact: true}).last();
  await last.click();
  const comparison = page.getByRole("table").last();
  assert.match(await comparison.innerText(), /Recorded.*New.*Comparison/s);
  assert.match(await comparison.innerText(), /68 cells.*68 cells.*Match/s);
  if (process.env.BQ_TEST_SCREENSHOT) await page.screenshot({path: process.env.BQ_TEST_SCREENSHOT});
  await page.getByRole("button", {name: "Export research bundle"}).click();
  const received = page.waitForEvent("download");
  await page.getByRole("link", {name: "Download research bundle"}).click();
  assert.match((await received).suggestedFilename(), /^bioquorum-.*\.zip$/);
  console.log("Workbench verification and bundle download passed");
}

(async () => {
  const server = spawn(process.env.BQ_TEST_PYTHON || ".venv/bin/python",
    ["tests/e2e/serve_workbench.py"], {stdio: ["ignore", "pipe", "inherit"]});
  let browser;
  try {
    const url = await serverURL(server);
    browser = await chromium.launch({
      channel: process.env.BQ_CHROME_CHANNEL || "chrome", headless: true,
      executablePath: process.env.BQ_CHROME_PATH,
    });
    const context = await browser.newContext({viewport: {width: 1440, height: 900}});
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    for (let attempt = 0; attempt < 20; attempt++) {
      try {
        await page.goto(url);
        break;
      } catch (error) {
        if (attempt === 19) throw error;
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
    }
    if (process.env.BQ_TEST_PROJECT_NOTE) {
      await verifyProjectNote(page);
      assert.deepEqual(errors, []);
      return;
    }
    if (process.env.BQ_TEST_REAL_PROJECT) {
      await verifyRealProject(page);
      assert.deepEqual(errors, []);
      return;
    }
    await page.getByRole("button", {name: "Try the metformin example"}).click();
    await page.getByRole("button", {name: "Submit plan"}).click();
    await page.getByText("Awaiting approval", {exact: true}).waitFor();
    assert.equal(await page.getByRole("heading", {name: "Findings"}).count(), 0);
    await page.reload();
    await page.getByRole("button", {name: "Approve and run"}).click();
    await page.getByRole("heading", {name: "Findings"}).waitFor();
    await page.getByText("Reviewed", {exact: true}).first().waitFor();
    await page.getByText("Assertion only", {exact: true}).waitFor();
    await page.getByRole("button", {name: "Audit log", exact: true}).click();
    const journal = page.getByRole("dialog", {name: "Audit log"});
    await journal.getByText(/Data classification/).waitFor();
    await journal.getByText("model call started", {exact: true}).first().waitFor();
    await journal.getByRole("button", {name: "Close audit log"}).click();
    const figure = page.getByRole("img", {name: "Figure from the recorded calculation"});
    await figure.evaluate((image) => image.scrollIntoView({block: "center"}));
    await page.waitForFunction(() => document.querySelector(".figure img")?.naturalWidth > 0);
    await page.getByRole("button", {name: "3", exact: true}).click();
    let drawer = page.getByRole("dialog", {name: "excerpts"});
    await drawer.getByRole("heading", {name: "Input hashes"}).waitFor();
    assert.match(await drawer.innerText(), /value\("excerpts"/);
    assert.match(await drawer.innerText(), /[0-9a-f]{64}/);
    await drawer.getByRole("button", {name: "Close evidence"}).click();
    await page.getByRole("button", {name: "[PMID:11118008]", exact: true}).click();
    drawer = page.getByRole("dialog", {name: "PMID:11118008"});
    assert.equal(await drawer.locator("mark").innerText(), "through a reduction in gluconeogenesis");
    await drawer.getByRole("button", {name: "Close evidence"}).click();
    await page.getByRole("button", {name: "Fork session", exact: true}).click();
    await page.getByLabel("Session title").fill("Compare a narrower set");
    await page.getByRole("button", {name: "Continue", exact: true}).click();
    await page.getByRole("heading", {name: "Compare a narrower set"}).waitFor();
    await page.reload();
    await page.getByRole("button", {name: "[PMID:11118008]", exact: true}).click();
    await page.getByRole("dialog", {name: "PMID:11118008"}).getByRole("button", {name: "Close evidence"}).click();
    if (process.env.BQ_TEST_SCREENSHOT) await page.screenshot({path: process.env.BQ_TEST_SCREENSHOT});
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.getByRole("link", {name: /Sessions/}).click();
    await page.getByRole("navigation", {name: "Sessions"}).getByRole("button", {name: "Compare a narrower set"}).click();
    await page.waitForFunction(() => document.activeElement === document.getElementById("session-name"));
    assert.deepEqual(errors, []);
    console.log("Workbench browser flow passed");
  } finally {
    await browser?.close();
    if (server.exitCode === null) {
      const exited = once(server, "exit");
      server.kill("SIGTERM");
      await exited;
    }
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
