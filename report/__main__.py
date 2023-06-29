import os
import sys
import time
import urllib.request
import uuid

import lxml.etree
import requests
from prettytable import PrettyTable

from dispatch.__init__ import (
    ARTIFACT_BASE_URL,
    TESTING_FARM_ENDPOINT,
    LATEST_TASKS_FILE,
    DEFAULT_TASKS_FILE,
    FormatText,
    get_arguments,
)

ARGS = get_arguments()


def parse_tasks():
    request_url_list = []
    tasks_source = None
    if ARGS.latest:
        if os.path.exists(LATEST_TASKS_FILE):
            tasks_source = LATEST_TASKS_FILE
            tasks_source_data = open(tasks_source).readlines()
        else:
            print(
                f"The path to the latest jobs file {LATEST_TASKS_FILE} does not exist!\nDo not use --latest to parse the default {DEFAULT_TASKS_FILE} path.\nOR\nuse --file for custom path."
            )
            sys.exit(1)
    elif ARGS.file:
        if os.path.exists(str(ARGS.file)):
            tasks_source = ARGS.file
            tasks_source_data = open(tasks_source).readlines()
        else:
            print(f"Given path {ARGS.file} does not exist!")
            sys.exit(1)
    elif ARGS.cmd:
        tasks_source_data = ARGS.cmd
    else:
        if os.path.exists(DEFAULT_TASKS_FILE):
            tasks_source = DEFAULT_TASKS_FILE
            tasks_source_data = open(tasks_source).readlines()
        else:
            print(
                f"Default file containing tasks doesn't exist.\nPass the tasks with --url or create and feed the {DEFAULT_TASKS_FILE}"
            )
            sys.exit(1)

    for task in tasks_source_data:
        task = task.strip().rstrip("/")

        if not task:
            continue
        elif task.startswith(TESTING_FARM_ENDPOINT):
            task = task
        elif task.startswith(ARTIFACT_BASE_URL):
            task = task.replace(ARTIFACT_BASE_URL, TESTING_FARM_ENDPOINT)
        elif task == str(uuid.UUID(task)):
            task = TESTING_FARM_ENDPOINT + "/" + task
        else:
            print(f"Unrecognized task {task}")
            continue

        # Validate UUID
        try:
            task_id = task.split("/")[-1]
            uuid.UUID(task_id)
        except ValueError:
            raise ValueError(task_id)

        request_url_list.append(task)

    return request_url_list, tasks_source


def parse_request_xunit(request_url_list=None, tasks_source=None):
    logs_base_directory = "/var/tmp/tesar/logs"

    if request_url_list is None or tasks_source is None:
        request_url_list, tasks_source = parse_tasks()
    if len(request_url_list) == 0 or all(element == "" for element in request_url_list):
        print("There are no tasks to report for.")
        print(
            f"Verify the {tasks_source} has at least one task in it or pass the values to the commandline with -c/--cmd."
        )
        sys.exit(1)

    parsed_dict = {}
    clear_line = "\x1b[2K"
    spacer = " " * 10
    loading_chars = ["/", "-", "\\", "|"]
    index = 0

    print("Reporting for the requested tasks:")
    for url in request_url_list:
        request = requests.get(url)
        request_state = request.json()["state"].upper()
        request_uuid = request.json()["id"]
        request_target = request.json()["environments_requested"][0]["os"]["compose"]
        request_datetime_created = request.json()["created"]
        request_datetime_parsed = request_datetime_created.split(".")[0]

        log_dir = f"{request_uuid}_logs"

        if request_state == "COMPLETE":
            request_state = FormatText.bg_green + request_state + FormatText.bg_default
        elif request_state == "QUEUED":
            request_state = FormatText.bg_blue + request_state + FormatText.bg_default
        elif request_state == "RUNNING":
            request_state = FormatText.bg_cyan + request_state + FormatText.bg_default
        elif request_state == "ERROR":
            request_state = FormatText.bg_yellow + request_state + FormatText.bg_default
        print(
            request.json()["environments_requested"][0]["os"]["compose"],
            request.json()["test"]["fmf"]["name"],
            url,
            request_state,
        )

        if ARGS.wait:
            while (
                request.json()["state"] != "complete"
                and request.json()["state"] != "error"
            ):
                print(end=clear_line)
                print(
                    f"Waiting for the job to finish.{spacer}{loading_chars[index]}",
                    end="\r",
                    flush=True,
                )
                index = (index + 1) % len(loading_chars)
                time.sleep(0.2)
            else:
                print("Job finished!")
        else:
            if (
                request.json()["state"] != "complete"
                and request.json()["state"] != "error"
            ):
                print(
                    f"Request {url} is still running, wait for it to finish or use --wait.\nSkipping to next request."
                )
                continue

        if request.json()["state"] == "error":
            error_formatted = FormatText.bg_red + "ERROR" + FormatText.bg_default
            error_reason = request.json()["result"]["summary"]
            message = f"""Request ended up in {error_formatted} state, because {error_reason}, thus won't be reported.
See more details on the result page {url.replace(TESTING_FARM_ENDPOINT, ARTIFACT_BASE_URL)}
Skipping to next request."""
            print(FormatText.bold + message + FormatText.end)
            continue

        if ARGS.download_logs:
            print("  > Downloading the log files.")
            # Create the log directory path for the request
            log_dir_path = os.path.join(logs_base_directory, log_dir)
            os.makedirs(log_dir_path, exist_ok=True)

        xunit = request.json()["result"]["xunit"]
        xml = lxml.etree.fromstring(xunit.encode())

        job_result_overall = xml.xpath("/testsuites/@overall-result")
        job_test_suite = xml.xpath("//testsuite")

        if request_uuid not in parsed_dict:
            parsed_dict[request_uuid] = {
                "target_name": request_target,
                "testsuites": [],
            }

        for elem in job_test_suite:
            testsuite_testcase = elem.xpath("./testcase")
            # With the latest Testing Farm release, the testsuite name does not include the target name
            # it consist of only the plan name
            testsuite_name = elem.xpath("./@name")[0].split(":")[-1]
            testsuite_result = elem.xpath("./@result")[0].upper()
            testsuite_test_count = elem.xpath("./@tests")
            testsuite_log_dir = testsuite_name.split("/")[-1]
            if testsuite_result == "PASSED":
                testsuite_result = FormatText.green + testsuite_result + FormatText.end
                testsuite_name = FormatText.green + testsuite_name + FormatText.end
            elif testsuite_result == "FAILED":
                testsuite_result = FormatText.red + testsuite_result + FormatText.end
                testsuite_name = FormatText.red + testsuite_name + FormatText.end
            elif testsuite_result == "ERROR":
                testsuite_result = FormatText.yellow + testsuite_result + FormatText.end
                testsuite_name = FormatText.yellow + testsuite_name + FormatText.end

            testsuite_data = {
                "testsuite_name": testsuite_name,
                "testsuite_result": testsuite_result,
                "testcases": [],
            }
            parsed_dict[request_uuid]["testsuites"].append(testsuite_data)

            if ARGS.download_logs:
                # Create the log directory path for the testsuite
                testsuite_log_dir_path = os.path.join(log_dir_path, testsuite_log_dir)
                os.makedirs(testsuite_log_dir_path, exist_ok=True)

            for test in testsuite_testcase:
                testcase_name = test.xpath("./@name")[0]
                testcase_result = test.xpath("./@result")[0].upper()
                testcase_log_url = test.xpath('./logs/log[@name="testout.log"]/@href')[
                    0
                ]
                log_name = f"{request_target}_{testcase_name.split('/')[-1]}.log"

                # print(testcase_log_url_fail)
                # sys.exit()

                if testcase_result == "PASSED":
                    testcase_result = (
                        FormatText.green + testcase_result + FormatText.end
                    )
                    testcase_name = FormatText.green + testcase_name + FormatText.end
                elif testcase_result == "FAILED":
                    testcase_result = FormatText.red + testcase_result + FormatText.end
                    testcase_name = FormatText.red + testcase_name + FormatText.end
                elif testcase_result == "ERROR":
                    testcase_result = (
                        FormatText.yellow + testcase_result + FormatText.end
                    )
                    testcase_name = FormatText.yellow + testcase_name + FormatText.end

                # Constructing the parsed dictionary
                testcase_data = {
                    "testcase_name": testcase_name,
                    "testcase_result": testcase_result,
                }
                testsuite_data["testcases"].append(testcase_data)

                if ARGS.download_logs:
                    response = urllib.request.urlopen(testcase_log_url)
                    log_data = response.read().decode("utf-8")
                    log_file_path = os.path.join(testsuite_log_dir_path, log_name)

                    with open(log_file_path, "w") as logfile:
                        logfile.write(log_data)

        if ARGS.download_logs:
            print(f"    > Logfiles stored in {log_dir_path}")

    return parsed_dict


def build_table():
    parsed_dict = parse_request_xunit()

    result_table = PrettyTable()

    if ARGS.level.lower() == "l1":
        result_table.field_names = ["UUID", "Target", "Test Plan", "Result"]
        for task_uuid, data in parsed_dict.items():
            target = data["target_name"]
            result_table.add_row(
                [
                    task_uuid,
                    target,
                    "",
                    "",
                ]
            )
            for testsuite_data in data["testsuites"]:
                testsuite_name = testsuite_data["testsuite_name"]
                testsuite_result = testsuite_data["testsuite_result"]
                result_table.add_row(["", "", testsuite_name, testsuite_result])

    elif ARGS.level.lower() == "l2":
        result_table.field_names = [
            "UUID",
            "Target",
            "Test Plan",
            "Test Case",
            "Result",
        ]
        for task_uuid, data in parsed_dict.items():
            target = data["target_name"]
            result_table.add_row([task_uuid, target, "", "", ""])
            for testsuite_data in data["testsuites"]:
                testsuite_name = testsuite_data["testsuite_name"]
                testsuite_result = testsuite_data["testsuite_result"]
                result_table.add_row(["", "", testsuite_name, "", testsuite_result])
                testcases = testsuite_data["testcases"]
                for testcase in testcases:
                    testcase_name = testcase["testcase_name"]
                    testcase_result = testcase["testcase_result"]
                    result_table.add_row(
                        [
                            "",
                            "",
                            "",
                            testcase_name,
                            testcase_result,
                        ]
                    )

    result_table.align = "l"

    return result_table


def main(result_table=None):
    if result_table is None:
        result_table = build_table()
    if result_table.rowcount > 0:
        print(result_table)
    else:
        print("Nothing to report!")