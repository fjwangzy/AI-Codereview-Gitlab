
import os
import re
import time
from urllib.parse import urljoin
import fnmatch
import requests

from biz.utils.log import logger


def filter_changes(changes: list):
    '''
    过滤数据，只保留支持的文件类型以及必要的字段信息
    '''
    # 从环境变量中获取支持的文件扩展名
    supported_extensions = os.getenv('SUPPORTED_EXTENSIONS', '.java,.py,.php').split(',')

    filter_deleted_files_changes = [change for change in changes if not change.get("deleted_file")]

    # 过滤 `new_path` 以支持的扩展名结尾的元素, 仅保留diff和new_path字段
    filtered_changes = [
        {
            'diff': item.get('diff', ''),
            'new_path': item['new_path'],
            'additions': len(re.findall(r'^\+(?!\+\+)', item.get('diff', ''), re.MULTILINE)),
            'deletions': len(re.findall(r'^-(?!--)', item.get('diff', ''), re.MULTILINE))
        }
        for item in filter_deleted_files_changes
        if any(item.get('new_path', '').endswith(ext) for ext in supported_extensions)
    ]
    return filtered_changes


class MergeRequestHandler:
    def __init__(self, webhook_data: dict, gitlab_token: str, gitlab_url: str):
        self.merge_request_iid = None
        self.webhook_data = webhook_data
        self.gitlab_token = gitlab_token
        self.gitlab_url = gitlab_url
        self.event_type = None
        self.project_id = None
        self.organization_id = None
        self.action = None
        self.parse_event_type()

    def parse_event_type(self):
        # 提取 event_type
        self.event_type = self.webhook_data.get('object_kind', None)
        if self.event_type == 'merge_request':
            self.parse_merge_request_event()

    def parse_merge_request_event(self):
        # 提取 Merge Request 的相关参数
        merge_request = self.webhook_data.get('object_attributes', {})
        self.merge_request_iid = merge_request.get('iid')
        self.project_id = merge_request.get('target_project_id')
        self.action = merge_request.get('action')
        
        # 提取 organization_id
        # 尝试从 source.web_url 或 target.web_url 解析
        target = merge_request.get('target', {})
        web_url = target.get('web_url') or target.get('http_url', '')
        if web_url:
             match = re.search(r'codeup\.aliyun\.com/([^/]+)/', web_url)
             if match:
                 self.organization_id = match.group(1)

    def get_merge_request_changes(self) -> list:
        # 检查是否为 Merge Request Hook 事件
        if self.event_type != 'merge_request':
            logger.warn(f"Invalid event type: {self.event_type}. Only 'merge_request' event is supported now.")
            return []

        # 优先使用 Yunxiao OpenAPI (GetCompare)
        if self.organization_id:
            try:
                target_branch = self.object_attributes.get('target_branch')
                source_branch = self.object_attributes.get('source_branch')
                if target_branch and source_branch:
                    changes = self.repository_compare(target_branch, source_branch)
                    if changes:
                        return changes
            except Exception as e:
                logger.error(f"Failed to get MR changes from Yunxiao using GetCompare: {e}")

        # Gitlab merge request changes API可能存在延迟，多次尝试
        max_retries = 3  # 最大重试次数
        retry_delay = 10  # 重试间隔时间（秒）
        for attempt in range(max_retries):
            # 调用 GitLab API 获取 Merge Request 的 changes
            # 云效 (Codeup) API 通常兼容 GitLab API v4
            url = urljoin(f"{self.gitlab_url}/",
                          f"api/v4/projects/{self.project_id}/merge_requests/{self.merge_request_iid}/changes?access_raw_diffs=true")
            headers = {
                'Private-Token': self.gitlab_token
            }
            response = requests.get(url, headers=headers, verify=False)
            logger.debug(
                f"Get changes response from Yunxiao/GitLab (attempt {attempt + 1}): {response.status_code}, {response.text}, URL: {url}")

            # 检查请求是否成功
            if response.status_code == 200:
                changes = response.json().get('changes', [])
                if changes:
                    return changes
                else:
                    logger.info(
                        f"Changes is empty, retrying in {retry_delay} seconds... (attempt {attempt + 1}/{max_retries}), URL: {url}")
                    time.sleep(retry_delay)
            else:
                logger.warn(f"Failed to get changes from Yunxiao/GitLab (URL: {url}): {response.status_code}, {response.text}")
                return []

        logger.warning(f"Max retries ({max_retries}) reached. Changes is still empty.")
        return []  # 达到最大重试次数后返回空列表

    def repository_compare(self, before: str, after: str):
        # 比较两个提交/分支之间的差异
        # 优先使用 Yunxiao OpenAPI
        if self.organization_id:
             url = f"{self.gitlab_url.rstrip('/')}/oapi/v1/codeup/organizations/{self.organization_id}/repositories/{self.project_id}/compares"
             headers = {
                'x-yunxiao-token': self.gitlab_token, # gitlab_token holds the Yunxiao token
                'Content-Type': 'application/json'
             }
             params = {
                 'from': before,
                 'to': after
             }
             response = requests.get(url, headers=headers, params=params, verify=False)
             logger.debug(f"Get compare response from Yunxiao {url} headers:{headers}  params:{params}: {response.status_code}")
             if response.status_code == 200:
                 try:
                     if not response.text:
                        logger.warn("Yunxiao API returned empty body for repository_compare")
                        return []
                     
                     # Debug logging for raw response (can be verbose, remove in production if needed)
                     logger.debug(f"Raw Yunxiao Compare Response: {response.text[:1000]}")
                        
                     data = response.json()
                 except Exception as e:
                     logger.error(f"Failed to decode JSON from Yunxiao: {e}, Response: {response.text}")
                     return []

                 # 云效API返回结构 check
                 # 文档示例 directly returning object with "diffs" key.
                 # 但也可能包裹在 result 中
                 diffs = []
                 if data.get('result'):
                     diffs = data.get('result', {}).get('diffs', [])
                 else:
                     diffs = data.get('diffs', [])
                 
                 # 转换 camelCase keys 到 snake_case 以兼容 filter_changes
                 # Yunxiao: newPath, deletedFile, diff
                 # GitLab expects: new_path, deleted_file, diff
                 converted_diffs = []
                 for item in diffs:
                     converted_item = item.copy()
                     if 'newPath' in item:
                         converted_item['new_path'] = item['newPath']
                     if 'oldPath' in item:
                         converted_item['old_path'] = item['oldPath']
                     if 'deletedFile' in item:
                         converted_item['deleted_file'] = item['deletedFile']
                     if 'renamedFile' in item:
                         converted_item['renamed_file'] = item['renamedFile']
                     if 'newFile' in item:
                         converted_item['new_file'] = item['newFile']
                     converted_diffs.append(converted_item)
                 
                 return converted_diffs
             else:
                 logger.warn(f"Failed to get compare from Yunxiao: {response.status_code}, {response.text}")
                 # Fallthrough to GitLab API compatibility attempt?
        return []

    def get_merge_request_commits(self) -> list:
        # 检查是否为 Merge Request Hook 事件
        if self.event_type != 'merge_request':
            return []

        # 如果能获取到 organization_id，优先使用 Yunxiao OpenAPI
        if self.organization_id:
             url = f"{self.gitlab_url.rstrip('/')}/oapi/v1/codeup/organizations/{self.organization_id}/repositories/{self.project_id}/changeRequests/{self.merge_request_iid}/commits"
             headers = {
                'x-yunxiao-token': self.gitlab_token, # gitlab_token holds the Yunxiao token
                'Content-Type': 'application/json'
             }
             response = requests.get(url, headers=headers, verify=False)
             logger.debug(f"Get MR commits response from Yunxiao {url}: {response.status_code}, {response.text}")
             if response.status_code == 200:
                 # Yunxiao API 返回的通常是 { "result": [...], "success": true } 或直接 list?
                 # 根据 GetChangeRequest 风格，可能是直接返回对象或 result。
                 # 假设 commits 列表在 result 字段中，或者直接返回列表。
                 # 查看文档风格，通常是 `result` 包含数据。但 GetChangeRequest 示例直接返回对象。
                 # 列表接口通常返回 `result` 数组。
                 data = response.json()
                 commits = []
                 if isinstance(data, list):
                     commits = data
                 elif data.get('result'):
                     commits = data.get('result')
                 else:
                     commits = data if isinstance(data, list) else []

                 # 转换 camelCase keys 到 snake_case 以兼容 GitLab 格式
                 converted_commits = []
                 for commit in commits:
                     new_commit = commit.copy()
                     # Basic fields
                     if 'authorName' in commit:
                         new_commit['author_name'] = commit['authorName']
                     if 'authorEmail' in commit:
                         new_commit['author_email'] = commit['authorEmail']
                     if 'authoredDate' in commit:
                         new_commit['authored_date'] = commit['authoredDate']
                     if 'committerName' in commit:
                         new_commit['committer_name'] = commit['committerName']
                     if 'committerEmail' in commit:
                         new_commit['committer_email'] = commit['committerEmail']
                     if 'committedDate' in commit:
                         new_commit['committed_date'] = commit['committedDate']
                     if 'shortId' in commit:
                         new_commit['short_id'] = commit['shortId']
                     if 'parentIds' in commit:
                         new_commit['parent_ids'] = commit['parentIds']
                     if 'webUrl' in commit:
                         new_commit['web_url'] = commit['webUrl']
                     # ensure title exists
                     if 'title' not in new_commit and 'message' in new_commit:
                         new_commit['title'] = new_commit['message'].split('\n')[0]
                     
                     converted_commits.append(new_commit)
                 return converted_commits
             else:
                 logger.warn(f"Failed to get MR commits from Yunxiao: {response.status_code}, {response.text}")
                 # Fallthrough to GitLab API compatibility attempt?

        # 调用 GitLab API 获取 Merge Request 的 commits (Fallback or if no Org ID)
        url = urljoin(f"{self.gitlab_url}/",
                      f"api/v4/projects/{self.project_id}/merge_requests/{self.merge_request_iid}/commits")
        headers = {
            'Private-Token': self.gitlab_token
        }
        response = requests.get(url, headers=headers, verify=False)
        logger.debug(f"Get commits response from Yunxiao/GitLab: {response.status_code}, {response.text}")
        # 检查请求是否成功
        if response.status_code == 200:
            return response.json()
        else:
            logger.warn(f"Failed to get commits: {response.status_code}, {response.text}")
            return []

    def get_yunxiao_merge_request_details(self):
        # 获取云效 MR 详情，用于获取 patchSetBizId 等信息
        if not self.organization_id:
            return None
        
        url = f"{self.gitlab_url.rstrip('/')}/oapi/v1/codeup/organizations/{self.organization_id}/repositories/{self.project_id}/changeRequests/{self.merge_request_iid}"
        headers = {
            'x-yunxiao-token': self.gitlab_token,
            'Content-Type': 'application/json'
        }
        response = requests.get(url, headers=headers, verify=False)
        logger.debug(f"Get MR details from Yunxiao: {response.status_code}")
        if response.status_code == 200:
            result = response.json().get('result') # 假设返回结构中包含 result
            if not result:
                result = response.json() # 或者直接是对象
            return result
        return None

    def add_merge_request_notes(self, review_result):
        # 添加评论到 Merge Request
        # 优先使用 Yunxiao OpenAPI
        if self.organization_id:
            mr_details = self.get_yunxiao_merge_request_details()
            patch_set_biz_id = None
            if mr_details and mr_details.get('related_patchset'):
                patch_set_biz_id = mr_details.get('related_patchset', {}).get('patchSetBizId')
            
            # 如果获取到了 patchSetBizId，则使用 Yunxiao API
            if patch_set_biz_id:
                url = f"{self.gitlab_url.rstrip('/')}/oapi/v1/codeup/organizations/{self.organization_id}/repositories/{self.project_id}/changeRequests/{self.merge_request_iid}/comments"
                headers = {
                    'x-yunxiao-token': self.gitlab_token,
                    'Content-Type': 'application/json'
                }
                data = {
                    "comment_type": "GLOBAL_COMMENT",
                    "content": review_result,
                    "draft": False,
                    "resolved": False,
                    "patchset_biz_id": patch_set_biz_id
                }
                response = requests.post(url, headers=headers, json=data, verify=False)
                logger.debug(f"Add comment to MR {self.merge_request_iid} (Yunxiao): {response.status_code}, {response.text}")
                if response.status_code == 200:
                    logger.info("Comment successfully added to merge request (Yunxiao).")
                    return
                else:
                    logger.warn(f"Failed to add comment via Yunxiao API: {response.status_code}, {response.text}. Fallback to GitLab API.")

        # Fallback to GitLab API
        url = urljoin(f"{self.gitlab_url}/",
                      f"api/v4/projects/{self.project_id}/merge_requests/{self.merge_request_iid}/notes")
        headers = {
            'Private-Token': self.gitlab_token,
            'Content-Type': 'application/json'
        }
        data = {
            'body': review_result
        }
        response = requests.post(url, headers=headers, json=data, verify=False)
        logger.debug(f"Add notes to Yunxiao/GitLab {url}: {response.status_code}, {response.text}")
        if response.status_code == 201:
            logger.info("Note successfully added to merge request.")
        else:
            logger.error(f"Failed to add note: {response.status_code}")
            logger.error(response.text)

    def target_branch_protected(self) -> bool:
        url = urljoin(f"{self.gitlab_url}/",
                      f"api/v4/projects/{self.project_id}/protected_branches")
        headers = {
            'Private-Token': self.gitlab_token,
            'Content-Type': 'application/json'
        }
        response = requests.get(url, headers=headers, verify=False)
        logger.debug(f"Get protected branches response from Yunxiao/GitLab: {response.status_code}, {response.text}")
        # 检查请求是否成功
        if response.status_code == 200:
            data = response.json()
            target_branch = self.webhook_data['object_attributes']['target_branch']
            return any(fnmatch.fnmatch(target_branch, item['name']) for item in data)
        else:
            logger.warn(f"Failed to get protected branches: {response.status_code}, {response.text}")
            return False


class PushHandler:
    def __init__(self, webhook_data: dict, gitlab_token: str, gitlab_url: str):
        self.webhook_data = webhook_data
        self.gitlab_token = gitlab_token
        self.gitlab_url = gitlab_url
        self.event_type = None
        self.project_id = None
        self.organization_id = None
        self.branch_name = None
        self.commit_list = []
        self.parse_event_type()

    def parse_event_type(self):
        # 提取 event_type
        self.event_type = self.webhook_data.get('object_kind', None)
        # 阿里云 Codeup Push 事件的 object_kind 是 'push'，与 GitLab 一致
        if self.event_type == 'push':
            self.parse_push_event()

    def parse_push_event(self):
        # 提取 Push 事件的相关参数
        self.project_id = self.webhook_data.get('project_id', None)
        if self.project_id is None:
            self.project_id = self.webhook_data.get('project', {}).get('id')
        self.branch_name = self.webhook_data.get('ref', '').replace('refs/heads/', '')
        self.commit_list = self.webhook_data.get('commits', [])
        
        # 提取 organization_id
        # 尝试从 git_http_url 中解析: https://codeup.aliyun.com/{org_id}/{repo_name}.git
        repository = self.webhook_data.get('repository', {})
        git_url = repository.get('git_http_url') or repository.get('url', '')
        if git_url:
            match = re.search(r'codeup\.aliyun\.com/([^/]+)/', git_url)
            if match:
                self.organization_id = match.group(1)

    def get_push_commits(self) -> list:
        # 检查是否为 Push 事件
        if self.event_type != 'push':
            logger.warn(f"Invalid event type: {self.event_type}. Only 'push' event is supported now.")
            return []

        # 提取提交信息
        commit_details = []
        for commit in self.commit_list:
            commit_info = {
                'message': commit.get('message'),
                'author': commit.get('author', {}).get('name'),
                'timestamp': commit.get('timestamp'),
                # Codeup 的 commit url 可能有所不同，但我们只需要 message 和 timestamp 用于 review logic
                'url': commit.get('url'),
            }
            commit_details.append(commit_info)

        logger.info(f"Collected {len(commit_details)} commits from push event.")
        return commit_details

    def add_push_notes(self, message: str):
        # 添加评论到 Push 请求的提交中
        if not self.commit_list:
            logger.warn("No commits found to add notes to.")
            return

        # 获取最后一个提交的ID
        last_commit_id = self.commit_list[-1].get('id')
        if not last_commit_id:
            logger.error("Last commit ID not found.")
            return

        url = urljoin(f"{self.gitlab_url}/",
                      f"api/v4/projects/{self.project_id}/repository/commits/{last_commit_id}/comments")
        headers = {
            'Private-Token': self.gitlab_token,
            'Content-Type': 'application/json'
        }
        data = {
            'note': message
        }
        response = requests.post(url, headers=headers, json=data, verify=False)
        logger.debug(f"Add comment to commit {last_commit_id}: {response.status_code}, {response.text}")
        if response.status_code == 201:
            logger.info("Comment successfully added to push commit.")
        else:
            logger.error(f"Failed to add comment: {response.status_code}")
            logger.error(response.text)

    def get_yunxiao_commit(self, commit_sha: str):
        # 使用 Yunxiao OpenAPI 获取单个提交详情
        # GET https://{domain}/oapi/v1/codeup/organizations/{organizationId}/repositories/{repositoryId}/commits/{sha}
        if not self.organization_id:
            logger.warn("Organization ID not found, cannot call Yunxiao API.")
            return None

        url = f"{self.gitlab_url.rstrip('/')}/oapi/v1/codeup/organizations/{self.organization_id}/repositories/{self.project_id}/commits/{commit_sha}"
        
        # Yunxiao token 需要在 header 'x-yunxiao-token' 或者 'Authorization' ? 
        # 文档参数示例: x-yunxiao-token: pt-0fh3****0fbG_35af****0484
        # self.gitlab_token 这里实际上存的是 yunxiao_token
        headers = {
            'x-yunxiao-token': self.gitlab_token,
            'Content-Type': 'application/json'
        }
        
        response = requests.get(url, headers=headers, verify=False)
        logger.debug(f"Get commit response from Yunxiao: {response.status_code}, {response.text}, URL: {url}")
        
        if response.status_code == 200:
            return response.json()
        else:
             logger.warn(f"Failed to get commit {commit_sha}: {response.status_code}, {response.text}")
             return None

    def get_parent_commit_id(self, commit_id: str) -> str:
        # 优先使用 Yunxiao OpenAPI
        commit_info = self.get_yunxiao_commit(commit_id)
        if commit_info and commit_info.get('result'):
             # Yunxiao API 响应通常包裹在 result 或直接返回对象，查看文档示例是直接返回对象
             # 但有时会有 result 字段 wrapping，文档示例直接是 object。
             # 假设直接是 object
             data = commit_info
             parent_ids = data.get('parentIds', [])
             if parent_ids:
                 return parent_ids[0]
        elif commit_info and commit_info.get('parentIds'):
             return commit_info.get('parentIds')[0]
             
        # Fallback to GitLab API mechanism if Yunxiao API fails or structure doesn't match
        # 注意：如果 Yunxiao 不支持 GitLab repository/commits 接口，这里会失败
        return ""

    def repository_compare(self, before: str, after: str):
        # 比较两个提交之间的差异
        # 优先使用 Yunxiao OpenAPI
        if self.organization_id:
             url = f"{self.gitlab_url.rstrip('/')}/oapi/v1/codeup/organizations/{self.organization_id}/repositories/{self.project_id}/compares"
             headers = {
                'x-yunxiao-token': self.gitlab_token, # gitlab_token holds the Yunxiao token
                'Content-Type': 'application/json'
             }
             params = {
                 'from': before,
                 'to': after
             }
             response = requests.get(url, headers=headers, params=params, verify=False)
             logger.debug(f"Get compare response from Yunxiao {url} headers:{headers}  params:{params}: {response.status_code}")
             if response.status_code == 200:
                 try:
                     if not response.text:
                        logger.warn("Yunxiao API returned empty body for repository_compare")
                        return []
                     
                     # Debug logging for raw response (can be verbose, remove in production if needed)
                     logger.debug(f"Raw Yunxiao Compare Response: {response.text[:1000]}")
                        
                     data = response.json()
                 except Exception as e:
                     logger.error(f"Failed to decode JSON from Yunxiao: {e}, Response: {response.text}")
                     return []

                 # 云效API返回结构 check
                 # 文档示例 directly returning object with "diffs" key.
                 # 但也可能包裹在 result 中
                 diffs = []
                 if data.get('result'):
                     diffs = data.get('result', {}).get('diffs', [])
                 else:
                     diffs = data.get('diffs', [])
                 
                 # 转换 camelCase keys 到 snake_case 以兼容 filter_changes
                 # Yunxiao: newPath, deletedFile, diff
                 # GitLab expects: new_path, deleted_file, diff
                 converted_diffs = []
                 for item in diffs:
                     converted_item = item.copy()
                     if 'newPath' in item:
                         converted_item['new_path'] = item['newPath']
                     if 'oldPath' in item:
                         converted_item['old_path'] = item['oldPath']
                     if 'deletedFile' in item:
                         converted_item['deleted_file'] = item['deletedFile']
                     if 'renamedFile' in item:
                         converted_item['renamed_file'] = item['renamedFile']
                     if 'newFile' in item:
                         converted_item['new_file'] = item['newFile']
                     converted_diffs.append(converted_item)
                 
                 return converted_diffs
             else:
                 logger.warn(f"Failed to get compare from Yunxiao: {response.status_code}, {response.text}")
                 # Fallthrough to GitLab API compatibility attempt?

        # Fallback to GitLab API
        url = f"{urljoin(f'{self.gitlab_url}/', f'api/v4/projects/{self.project_id}/repository/compare')}?from={before}&to={after}"
        headers = {
            'Private-Token': self.gitlab_token
        }
        response = requests.get(url, headers=headers, verify=False)
        logger.debug(
            f"Get changes response from Yunxiao/GitLab for repository_compare: {response.status_code}, {response.text}, URL: {url}")

        if response.status_code == 200:
            return response.json().get('diffs', [])
        else:
            logger.warn(
                f"Failed to get changes for repository_compare: {response.status_code}, {response.text}")
            return []

    def get_push_changes(self) -> list:
        # 检查是否为 Push 事件
        if self.event_type != 'push':
            logger.warn(f"Invalid event type: {self.event_type}. Only 'push' event is supported now.")
            return []

        # 如果没有提交，返回空列表
        if not self.commit_list:
            logger.info("No commits found in push event.")
            return []
        headers = {
            'Private-Token': self.gitlab_token
        }

        # 优先尝试compare API获取变更
        before = self.webhook_data.get('before', '')
        after = self.webhook_data.get('after', '')
        if before and after:
            if after.startswith('0000000'):
                # 删除分支处理
                return []
            if before.startswith('0000000'):
                # 创建分支处理
                first_commit_id = self.commit_list[0].get('id')
                parent_commit_id = self.get_parent_commit_id(first_commit_id)
                if parent_commit_id:
                    before = parent_commit_id
            return self.repository_compare(before, after)
        else:
            return []
