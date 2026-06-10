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

// random sleep between min and max milliseconds
function sleep(minMs, maxMs) {
  const ms = Math.floor(Math.random() * (maxMs - minMs + 1)) + minMs;
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// get no_email leads from SQLite via Python
function getLeads() {
  const dbPath =
    "/var/lib/docker/volumes/civ-lead-scraper_sqlite_data/_data/sales_agent.db";
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

// extract LinkedIn URL from Google redirect URL
function extractLinkedInUrl(googleUrl) {
  const match = googleUrl.split("&url=")[1];
  if (match) {
    return decodeURIComponent(match.split("&")[0]);
  }
  return null;
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
    ? `"${companyName}" "${city}" "${state}" "${decisionMakerName}" site:linkedin.com`
    : `"${companyName}" "${city}" "${state}" CEO OR President OR Principal site:linkedin.com`;

  const searchUrl = `https://www.google.com/search?q=${encodeURIComponent(query)}`;

  console.log(`Searching Google: ${query}`);

  await page.goto(searchUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
  await sleep(3000, 5000);

  // extract all hrefs from search results
  const hrefs = await page.evaluate(() => {
    const links = Array.from(document.querySelectorAll("a[href]"));
    return links
      .map((a) => a.href)
      .filter(
        (href) =>
          href.includes("linkedin.com/in/") ||
          (href.includes("google.com/url") &&
            href.includes("linkedin.com/in/")),
      );
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

  // clear input and type LinkedIn URL
  await page.click("#linkedin-url");
  await page.evaluate(() => {
    document.querySelector("#linkedin-url").value = "";
  });
  await page.type("#linkedin-url", linkedinUrl, { delay: 80 });

  await sleep(1000, 2000);

  // click Find Email button
  await page.click('button[aria-label="Find Email"]');

  console.log("Waiting for Mailmeteor result...");
  await sleep(15000, 20000);

  // check for email result
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

  // check for no results
  const noResult = await page
    .$eval("span", (els) =>
      [...document.querySelectorAll("span")].find((el) =>
        el.textContent.includes("No results found"),
      ),
    )
    .catch(() => null);

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

  const results = [];

  const browser = await puppeteer.launch({
    headless: true,
    args: [
      "--no-sandbox",
      "--disable-dev-shm-usage",
      "--disable-blink-features=AutomationControlled",
    ],
  });

  try {
    const page = await browser.newPage();

    // set realistic viewport
    await page.setViewport({ width: 1280, height: 800 });

    for (const lead of leads) {
      console.log(
        `\nProcessing: ${lead.company_name} (${lead.city}, ${lead.state})`,
      );

      try {
        // step 1 — find LinkedIn URL via Google
        const linkedinUrl = await findLinkedInUrl(
          page,
          lead.company_name,
          lead.city,
          lead.state,
          lead.decision_maker_name,
        );

        if (!linkedinUrl) {
          results.push({
            outreach_id: lead.id,
            place_id: lead.place_id,
            company_name: lead.company_name,
            email: null,
            status: "no_linkedin",
          });
          await sleep(10000, 15000);
          continue;
        }

        await sleep(10000, 15000);

        // step 2 — find email via Mailmeteor
        const email = await findEmailViaMailmeteor(page, linkedinUrl);

        results.push({
          outreach_id: lead.id,
          place_id: lead.place_id,
          company_name: lead.company_name,
          email: email,
          linkedin_url: linkedinUrl,
          status: email ? "found" : "not_found",
        });

        await sleep(10000, 15000);
      } catch (e) {
        console.error(`Error processing ${lead.company_name}: ${e.message}`);
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
  } finally {
    await browser.close();
  }

  // write results to JSON
  const outputPath = path.join(__dirname, "results.json");
  fs.writeFileSync(outputPath, JSON.stringify(results, null, 2));

  const found = results.filter((r) => r.email).length;
  const notFound = results.filter((r) => !r.email).length;

  console.log(`\nDone — found=${found} not_found=${notFound}`);
  console.log(`Results written to ${outputPath}`);
  console.log("Now run: python3 email_finder/update_db.py");
}

main().catch(console.error);
