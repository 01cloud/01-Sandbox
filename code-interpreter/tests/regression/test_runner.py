#!/usr/bin/env python3
"""
OpenSandbox API-Driven Security Scan Regression Test Harness
============================================================
Validates Checkov, Trivy, Kubesec, Semgrep, and other scanner detections against
malicious K8s YAML fixtures by communicating directly with the API Server.
Does NOT require a local Docker daemon.
"""

import argparse
import json
import os
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Color palettes for premium console reporting
GREEN = "\033[38;5;46m"
RED = "\033[38;5;196m"
YELLOW = "\033[38;5;220m"
BLUE = "\033[38;5;39m"
PURPLE = "\033[38;5;135m"
CYAN = "\033[38;5;51m"
BOLD = "\033[1m"
RESET = "\033[0m"

BANNER = f"""{CYAN}{BOLD}
   ▄████████  ▄██████▄   ███        ▄████████  ▄██████▄  ▀█████████▄   ▄██████▄  ▄██   ▄      
  ███    ███ ███    ███  ███       ███    ███ ███    ███   ███    ███ ███    ███ ███   ██▄    
  ███    █▀  ███    ███  ███       ███    █▀  ███    ███   ███    ███ ███    ███ ███▄▄▄███    
 ▄███▄▄▄     ███    ███  ███      ▄███▄▄▄     ███    ███  ▄███▄▄▄██▀  ███    ███ ▀▀▀▀▀▀███    
▀▀███▀▀▀     ███    ███  ███     ▀▀███▀▀▀     ███    ███ ▀▀███▀▀▀██▄  ███    ███ ▄██   ███    
  ███        ███    ███  ███       ███        ███    ███   ███    ██▄ ███    ███ ███   ███    
  ███        ██S    ███  ███▌    ▄ ███        ███    ███   ███    ███ ███    ███ ███   ███    
  ███         ▀██████▀   █████▄▄██ ██████████  ▀██████▀  ▄█████████▀   ▀██████▀   ▀██████▀     
                         ▀                                                                  
              {YELLOW}--- REMOTE API SECURITY REGRESSION TESTING SUITE v1.0.0 ---{RESET}
"""

def print_divider(char="─", length=90, color=BLUE):
    print(f"{color}{char * length}{RESET}")

def load_expected_assertions(expected_dir):
    expected = {}
    if not os.path.exists(expected_dir):
        return expected
    for name in os.listdir(expected_dir):
        if name.endswith(".json"):
            path = os.path.join(expected_dir, name)
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                    if data.get("filename"):
                        expected[data["filename"]] = data.get("expected_findings", [])
            except Exception as e:
                print(f"{RED}Warning: Failed to load expectation file {name}: {e}{RESET}")
    return expected

def test_api_scan(api_url, api_key, fixture_path, expected_findings):
    """Sends the fixture to the remote API and validates the scanner findings."""
    filename = os.path.basename(fixture_path)
    print(f" {BLUE}Remote Scan via API:{RESET} {BOLD}{filename}{RESET}")
    
    with open(fixture_path, 'r') as f:
        content = f.read()
        
    payload = json.dumps({"files": {filename: content}}).encode("utf-8")
    req = Request(
        f"{api_url.rstrip('/')}/v1/scan-jobs",
        method="POST",
        headers={
            "accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}" if not api_key.startswith("Bearer") else api_key
        },
        data=payload
    )
    
    start_time = time.time()
    findings_matched = []
    findings_missing = []
    overall_status = "FAILED"
    
    try:
        response = urlopen(req)
        duration = time.time() - start_time
        res_data = json.loads(response.read().decode('utf-8'))
        
        report_str = json.dumps(res_data).lower()
        for item in expected_findings:
            if item.lower() in report_str:
                findings_matched.append(item)
            else:
                findings_missing.append(item)
                
        overall_status = "PASS" if not findings_missing else "FAIL"
        
    except HTTPError as e:
        duration = time.time() - start_time
        if e.code == 429:
            overall_status = "RATE_LIMITED"
            print(f"  {YELLOW}Rate Limit Hit (429)! Sleep and retry recommended.{RESET}")
        else:
            overall_status = f"HTTP_{e.code}"
            print(f"  {RED}API Error {e.code}: {e.read().decode('utf-8', errors='ignore')}{RESET}")
    except Exception as e:
        duration = time.time() - start_time
        overall_status = "ERROR"
        print(f"  {RED}Network Error: {e}{RESET}")
        
    return {
        "status": overall_status,
        "duration": duration,
        "matched": findings_matched,
        "missing": findings_missing
    }

def test_api_unauthorized(api_url):
    print(f" {BLUE}Testing Gatekeeper:{RESET} Ingesting malicious YAML without a valid token...")
    req = Request(
        f"{api_url.rstrip('/')}/v1/scan-jobs",
        method="POST",
        headers={
            "accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "Bearer bad-expired-token-signature"
        },
        data=json.dumps({"files": {"input.yaml": "apiVersion: v1"}}).encode("utf-8")
    )
    
    try:
        urlopen(req)
        return "FAIL", "API accepted a bogus authorization signature."
    except HTTPError as e:
        if e.code == 401:
            return "PASS", f"Blocked unauthorized key cleanly: HTTP {e.code}"
        else:
            return "FAIL", f"Unexpected gateway status code: HTTP {e.code}"
    except Exception as e:
        return "ERROR", f"Failed to connect to API gateway: {e}"

def test_api_rate_limiting(api_url, api_key):
    print(f" {BLUE}Testing Rate Limiter:{RESET} Sending rapid-fire requests to exhaust key quota...")
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}" if not api_key.startswith("Bearer") else api_key
    }
    payload = json.dumps({"files": {"input.yaml": "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: test"}}).encode("utf-8")
    
    success_count = 0
    blocked_cleanly = False
    
    for i in range(1, 11):
        req = Request(f"{api_url.rstrip('/')}/v1/scan-jobs", method="POST", headers=headers, data=payload)
        try:
            urlopen(req)
            success_count += 1
            print(f"  -> Request #{i} accepted.")
            time.sleep(0.1) 
        except HTTPError as e:
            if e.code == 429:
                blocked_cleanly = True
                print(f"  -> {GREEN}Blocked at Request #{i} cleanly: HTTP 429 Rate Limit Exceeded!{RESET}")
                break
            else:
                print(f"  -> Rejected with unexpected status: HTTP {e.code}")
                break
        except Exception as e:
            print(f"  -> Network error: {e}")
            break
            
    if blocked_cleanly:
        return "PASS", f"Rate limiter locked after {success_count} requests within the window."
    else:
        return "FAIL", f"Completed {success_count}/10 requests without hitting a 429 rate limit. Is rate limiting active?"

def main():
    parser = argparse.ArgumentParser(description="Remote Security Scan Regression Tester")
    parser.add_argument("--api-url", required=True, help="FastAPI Base URL for gateway (e.g. http://server-ip:8000)")
    parser.add_argument("--api-key", required=True, help="Valid developer API key for live testing")
    parser.add_argument("--delay", type=float, default=2.0, help="Cool-down interval between successive scans (seconds)")
    
    args = parser.parse_args()
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    fixtures_dir = os.path.join(base_dir, "fixtures")
    expected_dir = os.path.join(base_dir, "expected")
    
    print(BANNER)
    print_divider("━")
    print(f" {BOLD}REMOTE GATEWAY API:{RESET}  {BLUE}{args.api_url}{RESET}")
    print(f" {BOLD}API KEY LENGTH:{RESET}      {len(args.api_key)} chars")
    print(f" {BOLD}SCAN COOLDOWN:{RESET}       {YELLOW}{args.delay} seconds{RESET}")
    print_divider("━")
    
    expected_assertions = load_expected_assertions(expected_dir)
    fixtures = [f for f in os.listdir(fixtures_dir) if f.endswith(".yaml")]
    fixtures.sort()
    
    if not fixtures:
        print(f"{RED}Error: No YAML fixtures found in {fixtures_dir}{RESET}")
        sys.exit(1)
        
    passed = 0
    failed = 0
    skipped = 0
    total_duration = 0.0
    
    print(f"\n{BOLD}{PURPLE}=== PHASE A: REMOTE VULNERABILITY AUDIT (VIA API) ==={RESET}\n")
    
    for idx, fixture_name in enumerate(fixtures, 1):
        fixture_path = os.path.join(fixtures_dir, fixture_name)
        assertions = expected_assertions.get(fixture_name, [])
        
        res = test_api_scan(args.api_url, args.api_key, fixture_path, assertions)
        total_duration += res["duration"]
        
        status_text = ""
        if res["status"] == "PASS":
            passed += 1
            status_text = f"{GREEN}{BOLD}[ PASS ]{RESET}"
        elif res["status"] == "FAIL":
            failed += 1
            status_text = f"{RED}{BOLD}[ FAIL ]{RESET}"
        else:
            failed += 1
            status_text = f"{RED}{BOLD}[ {res['status']} ]{RESET}"
            
        print(f"  Status: {status_text} | API Latency: {res['duration']:.2f}s")
        if res["matched"]:
            print(f"  Matched Violations: {GREEN}{', '.join(res['matched'])}{RESET}")
        if res["missing"]:
            print(f"  {RED}Missing Violations: {', '.join(res['missing'])}{RESET}")
        print()
        
        # Cooldown prevents rate limit hits during scanning
        if idx < len(fixtures):
            time.sleep(args.delay)
            
    print(f"\n{BOLD}{PURPLE}=== PHASE B: GATEWAY ACCESS CONTROL & RATE-LIMIT ENFORCEMENT ==={RESET}\n")
    
    auth_status, auth_details = test_api_unauthorized(args.api_url)
    if auth_status == "PASS": passed += 1
    elif auth_status == "SKIP": skipped += 1
    else: failed += 1
    print(f"  Status: {GREEN if auth_status == 'PASS' else RED}[ {auth_status} ]{RESET} | Details: {auth_details}\n")
    
    # Wait for the cooldown window before testing rate limit so our previous scans don't pollute the window
    print(f" {YELLOW}Waiting 10s cooldown before rapid-fire rate limit test...{RESET}")
    time.sleep(10)
    
    rl_status, rl_details = test_api_rate_limiting(args.api_url, args.api_key)
    if rl_status == "PASS": passed += 1
    elif rl_status == "SKIP": skipped += 1
    else: failed += 1
    print(f"  Status: {GREEN if rl_status == 'PASS' else (YELLOW if rl_status == 'SKIP' else RED)}[ {rl_status} ]{RESET} | Details: {rl_details}\n")
    
    print_divider("═")
    print(f" {BOLD}{CYAN}REMOTE REGRESSION TEST VERDICT AND PERFORMANCE SCORECARD{RESET}")
    print_divider("─")
    
    total_tests = passed + failed + skipped
    
    print(f" {BOLD}Total API Calls Validated:{RESET} {total_tests}")
    print(f" {BOLD}Tests Passed:{RESET}               {GREEN}{passed}{RESET}")
    print(f" {BOLD}Tests Failed:{RESET}               {RED if failed > 0 else GREEN}{failed}{RESET}")
    print(f" {BOLD}Tests Skipped:{RESET}              {YELLOW}{skipped}{RESET}")
    print(f" {BOLD}Total Network Latency:{RESET}      {total_duration:.2f} seconds")
    
    if failed == 0:
        verdict = f"{GREEN}{BOLD}STABLE (0 REGRESSIONS DETECTED){RESET}"
    else:
        verdict = f"{RED}{BOLD}UNSTABLE ({failed} SCAN REGRESSIONS DETECTED){RESET}"
        
    print(f" {BOLD}API Gateway Health:{RESET}         {verdict}")
    print_divider("═")
    
    if failed > 0:
        sys.exit(1)
    sys.exit(0)

if __name__ == "__main__":
    main()
