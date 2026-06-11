/*
email finder using puppeteer and mail meteor

for each no_email lead:
1. google search for linkedin profile
2. extract linkedin url from google result
3. submit to mailmeteor
4. scrape email if found
5. write results to json for python to update sqlite

*/

const puppeteer = require("puppeteer-extra");
const StealthPlugin = require("puppeteer-extra-plugin-stealth");
const AnonymizeUAPlugin = require("puppeteer-extra-plugin-anonymize-ua");
const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");

puppeteer.use(StealthPlugin());
puppeteer.use(AnonymizeUAPlugin());
require("dotenv").config({ path: path.join(__dirname, "..", ".env") });

// random sleep between min and max milliseconds
function sleep(minMs, maxMs) {
  const ms = Math.floor(Math.random() * (maxMs - minMs + 1)) + minMs;
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// get no_email leads from SQLite via Python
function getLeads() {
  //test mode
  const localLeadsPath = path.join(__dirname, "leads.json");
  if (fs.existsSync(localLeadsPath)) {
    console.log("Test mode — reading from leads.json");
    return JSON.parse(fs.readFileSync(localLeadsPath, "utf8"));
  }

  const dbPath = process.env.SALES_AGENT_DB_PATH;
  const result = execSync(
    `python3 -c "
import sqlite3, json
conn = sqlite3.connect('${dbPath}')
conn.row_factory = sqlite3.Row
rows = conn.execute('''SELECT o.id, o.place_id, cr.company_name, cr.city, cr.state, cr.decision_maker_name, cr.decision_maker_title FROM outreach o JOIN company_research cr ON o.place_id = cr.place_id WHERE o.status = 'no_email' LIMIT 50''').fetchall()
print(json.dumps([dict(r) for r in rows]))
conn.close()
"`,
    { cwd: path.join(__dirname, "..") },
  )
    .toString()
    .trim();

  return JSON.parse(result);
}

// search Google for LinkedIn profile
async function findLinkedInUrl(
  page,
  companyName,
  city,
  state,
  decisionMakerName,
) {
  const query = decisionMakerName
    ? `${companyName} ${state} ${decisionMakerName} linkedin`
    : `${companyName} ${state} CEO linkedin`;

  const searchUrl = `https://duckduckgo.com/?q=${encodeURIComponent(query)}&ia=web`;

  console.log(`Searching Google: ${query}`);

  await page.goto(searchUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
  await sleep(3000, 5000);

  // extract all hrefs from search results
  const hrefs = await page.evaluate(() => {
    const links = Array.from(document.querySelectorAll("a[href]"));
    return links
      .map((a) => a.href)
      .filter((href) => href.includes("linkedin.com/in/"));
  });

  for (const href of hrefs) {
    let linkedinUrl = null;

    if (href.includes("google.com/url")) {
      linkedinUrl = extractLinkedInUrl(href);
    } else if (href.includes("linkedin.com/in/")) {
      linkedinUrl = href;
    }

    if (linkedinUrl && linkedinUrl.includes("linkedin.com/in/")) {
      // normalise to www.linkedin.com
      linkedinUrl = linkedinUrl.replace("uk.linkedin.com", "www.linkedin.com");
      console.log(`Found LinkedIn URL: ${linkedinUrl}`);
      return linkedinUrl;
    }
  }

  console.log(`No LinkedIn URL found for ${companyName}`);
  return null;
}

// find email via Mailmeteor
async function findEmailViaMailmeteor(page, linkedinUrl) {
  console.log(`Submitting to Mailmeteor: ${linkedinUrl}`);

  await page.goto("https://mailmeteor.com/tools/linkedin-email-finder", {
    waitUntil: "domcontentloaded",
    timeout: 30000,
  });

  await sleep(3000, 5000);

  await page.click("#linkedin-url");
  await page.evaluate(() => {
    document.querySelector("#linkedin-url").value = "";
  });
  await page.type("#linkedin-url", linkedinUrl, { delay: 80 });

  await sleep(1000, 2000);

  await page.click('button[aria-label="Find Email"]');

  console.log("Waiting for Mailmeteor result...");

  // wait for either email result or no results message — up to 25 seconds
  try {
    await page.waitForSelector(
      'span.linkedin-email-finder__text.text-secondary, span:has-text("No results found")',
      { timeout: 25000 },
    );
  } catch (e) {
    console.log("Timed out waiting for result");
  }

  await sleep(2000, 3000);

  const emailElement = await page.$(
    "span.linkedin-email-finder__text.text-secondary",
  );
  if (emailElement) {
    const email = await emailElement.evaluate((el) => el.textContent.trim());
    if (email && email.includes("@")) {
      console.log(`Email found: ${email}`);
      return email;
    }
  }

  console.log("No email found");
  return null;
}

async function main() {
  const leads = getLeads();
  console.log(`Processing ${leads.length} leads with no email`);

  if (leads.length === 0) {
    console.log("No leads to process");
    return;
  }

  const browser = await puppeteer.launch({
    headless: false,
    executablePath: process.env.CHROMIUM_PATH,
    args: [
      "--no-sandbox",
      "--disable-dev-shm-usage",
      "--disable-blink-features=AutomationControlled",
    ],
  });

  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 800 });

    // ── PASS 1: collect LinkedIn URLs ──────────────────────────────
    console.log("\n── Pass 1: Finding LinkedIn URLs ──");
    const linkedinData = [];

    for (const lead of leads) {
      console.log(
        `\nSearching: ${lead.company_name} (${lead.city}, ${lead.state})`,
      );
      try {
        const linkedinUrl = await findLinkedInUrl(
          page,
          lead.company_name,
          lead.city,
          lead.state,
          lead.decision_maker_name,
        );

        linkedinData.push({
          ...lead,
          linkedin_url: linkedinUrl,
        });

        await sleep(10000, 15000);
      } catch (e) {
        console.error(
          `Error finding LinkedIn for ${lead.company_name}: ${e.message}`,
        );
        linkedinData.push({ ...lead, linkedin_url: null });
        await sleep(10000, 15000);
      }
    }

    // save LinkedIn URLs
    const linkedinPath = path.join(__dirname, "linkedin_urls.json");
    fs.writeFileSync(linkedinPath, JSON.stringify(linkedinData, null, 2));
    console.log(`\nPass 1 complete — saved to linkedin_urls.json`);

    const withLinkedin = linkedinData.filter((l) => l.linkedin_url);
    console.log(`Found LinkedIn URLs: ${withLinkedin.length}/${leads.length}`);

    // ── PASS 2: find emails via Mailmeteor ─────────────────────────
    console.log("\n── Pass 2: Finding Emails via Mailmeteor ──");
    const results = [];

    for (const lead of linkedinData) {
      if (!lead.linkedin_url) {
        results.push({
          outreach_id: lead.id,
          place_id: lead.place_id,
          company_name: lead.company_name,
          email: null,
          status: "no_linkedin",
        });
        continue;
      }

      console.log(`\nMailmeteor: ${lead.company_name}`);
      try {
        const email = await findEmailViaMailmeteor(page, lead.linkedin_url);

        results.push({
          outreach_id: lead.id,
          place_id: lead.place_id,
          company_name: lead.company_name,
          email: email,
          linkedin_url: lead.linkedin_url,
          status: email ? "found" : "not_found",
        });

        await sleep(10000, 15000);
      } catch (e) {
        console.error(
          `Error finding email for ${lead.company_name}: ${e.message}`,
        );
        results.push({
          outreach_id: lead.id,
          place_id: lead.place_id,
          company_name: lead.company_name,
          email: null,
          status: "error",
          error: e.message,
        });
        await sleep(10000, 15000);
      }
    }

    // write results
    const outputPath = path.join(__dirname, "results.json");
    fs.writeFileSync(outputPath, JSON.stringify(results, null, 2));

    const found = results.filter((r) => r.email).length;
    const notFound = results.filter((r) => !r.email).length;

    console.log(`\nDone — found=${found} not_found=${notFound}`);
    console.log(`Results written to results.json`);
    console.log("Now run: python3 email_finder/update_db.py");
  } finally {
    await browser.close();
  }
}
main().catch(console.error);
