const fs = require("fs");
const path = require("path");

const appRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(appRoot, "..");
const sourcePath = path.resolve(repoRoot, "data", "tags", "defined_tags.json");
const targetDir = path.resolve(appRoot, "generated");
const targetPath = path.join(targetDir, "defined_tags.json");

function main() {
  if (!fs.existsSync(sourcePath)) {
    console.warn(
      `defined_tags source not found at ${sourcePath}. Skipping sync.`
    );
    return;
  }
  fs.mkdirSync(targetDir, { recursive: true });
  const contents = fs.readFileSync(sourcePath);
  fs.writeFileSync(targetPath, contents);
  console.log(`Synced defined_tags.json -> ${path.relative(appRoot, targetPath)}`);
}

main();
