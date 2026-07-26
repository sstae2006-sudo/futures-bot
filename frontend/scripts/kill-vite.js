import { execSync } from "child_process";

try {
  if (process.platform === "win32") {
    execSync("taskkill /F /IM node.exe", { stdio: "ignore" });
  } else {
    execSync("pkill -f vite", { stdio: "ignore" });
  }
} catch {
  // No old process running
}