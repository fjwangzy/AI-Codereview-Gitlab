#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from unittest import TestCase, main
from unittest.mock import patch, MagicMock

from biz.platforms.yunxiao.webhook_handler import PushHandler, MergeRequestHandler


class TestYunxiaoPushHandler(TestCase):
    def setUp(self):
        """设置测试环境"""
        self.sample_webhook_data = {
            'object_kind': 'push',
            'project': {
                'id': 123
            },
            'repository': {
                 'git_http_url': 'https://codeup.aliyun.com/my-org/my-repo.git'
            },
            'ref': 'refs/heads/master',
            'commits': [
                {
                    'id': 'commit1',
                    'message': 'feat: initial commit',
                    'author': {'name': 'test_user'},
                    'timestamp': '2023-01-01T00:00:00Z',
                    'url': 'http://yunxiao/commit/commit1'
                }
            ],
            'aliyun_pk': 'test_pk'
        }
        self.yunxiao_token = 'test_token'
        self.yunxiao_url = 'https://codeup.aliyun.com'

        # 创建PushHandler实例
        self.handler = PushHandler(self.sample_webhook_data, self.yunxiao_token, self.yunxiao_url)

    def test_organization_id_extraction(self):
        self.assertEqual(self.handler.organization_id, 'my-org')

    def test_get_push_commits(self):
        commits = self.handler.get_push_commits()
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0]['message'], 'feat: initial commit')

    @patch('requests.get')
    def test_get_parent_commit_id(self, mock_get):
        # Mock Yunxiao GetCommit response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'id': 'commit1',
            'parentIds': ['parent_commit']
        }
        mock_get.return_value = mock_response

        parent_id = self.handler.get_parent_commit_id('commit1')
        self.assertEqual(parent_id, 'parent_commit')
        
        # Verify URL called
        expected_url = f"{self.yunxiao_url}/oapi/v1/codeup/organizations/my-org/repositories/123/commits/commit1"
        mock_get.assert_called_with(expected_url, headers={'x-yunxiao-token': 'test_token', 'Content-Type': 'application/json'}, verify=False)

    @patch('requests.get')
    def test_repository_compare(self, mock_get):
        # Mock Yunxiao GetCompare response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'diffs': [
                {'diff': 'test_diff', 'newPath': 'test.py'}
            ]
        }
        mock_get.return_value = mock_response

        diffs = self.handler.repository_compare('sha1', 'sha2')
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0]['newPath'], 'test.py')

        # Verify URL and params
        expected_url = f"{self.yunxiao_url}/oapi/v1/codeup/organizations/my-org/repositories/123/compares"
        mock_get.assert_called_with(expected_url, headers={'x-yunxiao-token': 'test_token', 'Content-Type': 'application/json'}, params={'from': 'sha1', 'to': 'sha2'}, verify=False)

    @patch('requests.post')
    def test_add_push_notes(self, mock_post):
        # Mock Yunxiao CreateCommitComment response
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_post.return_value = mock_response

        self.handler.add_push_notes("test comment")

        expected_url = f"{self.yunxiao_url}/oapi/v1/codeup/organizations/my-org/repositories/123/commits/commit1/comments"
        mock_post.assert_called_with(expected_url, headers={'x-yunxiao-token': 'test_token', 'Content-Type': 'application/json'}, json={'content': 'test comment'}, verify=False)


class TestYunxiaoMergeRequestHandler(TestCase):
    def setUp(self):
        self.sample_webhook_data = {
            'object_kind': 'merge_request',
            'object_attributes': {
                'iid': 1,
                'target_project_id': 123,
                'action': 'open',
                'target_branch': 'master',
                'target': {
                    'web_url': 'https://codeup.aliyun.com/my-org/my-repo'
                }
            },
            'aliyun_pk': 'test_pk'
        }
        self.yunxiao_token = 'test_token'
        self.yunxiao_url = 'https://codeup.aliyun.com'
        self.handler = MergeRequestHandler(self.sample_webhook_data, self.yunxiao_token, self.yunxiao_url)

    def test_organization_id_extraction(self):
        self.assertEqual(self.handler.organization_id, 'my-org')

    @patch('requests.get')
    def test_get_merge_request_changes(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'changes': [{'new_path': 'test.py', 'diff': '+ print("hello")'}]}
        mock_get.return_value = mock_response

        changes = self.handler.get_merge_request_changes()
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['new_path'], 'test.py')

    @patch('requests.get')
    def test_get_merge_request_commits(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'result': [{'id': 'c1', 'message': 'feat: test'}]}
        mock_get.return_value = mock_response

        commits = self.handler.get_merge_request_commits()
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0]['id'], 'c1')
        
        # Verify URL called
        expected_url = f"{self.yunxiao_url}/oapi/v1/codeup/organizations/my-org/repositories/123/changeRequests/1/commits"
        mock_get.assert_called_with(expected_url, headers={'x-yunxiao-token': 'test_token', 'Content-Type': 'application/json'}, verify=False)

    @patch('requests.post')
    @patch('requests.get')
    def test_add_merge_request_notes(self, mock_get, mock_post):
        # Mock get MR details for patchSetBizId
        mock_get_response = MagicMock()
        mock_get_response.status_code = 200
        mock_get_response.json.return_value = {
            'result': {
                'related_patchset': {'patchSetBizId': 'patch_123'}
            }
        }
        mock_get.return_value = mock_get_response

        # Mock post comment
        mock_post_response = MagicMock()
        mock_post_response.status_code = 200
        mock_post.return_value = mock_post_response

        self.handler.add_merge_request_notes("Excellent work")

        # Verify Get details called
        expected_get_url = f"{self.yunxiao_url}/oapi/v1/codeup/organizations/my-org/repositories/123/changeRequests/1"
        mock_get.assert_called_with(expected_get_url, headers={'x-yunxiao-token': 'test_token', 'Content-Type': 'application/json'}, verify=False)

        # Verify Post comment called
        expected_post_url = f"{self.yunxiao_url}/oapi/v1/codeup/organizations/my-org/repositories/123/changeRequests/1/comments"
        expected_data = {
            "comment_type": "GLOBAL_COMMENT",
            "content": "Excellent work",
            "draft": False,
            "resolved": False,
            "patchset_biz_id": "patch_123"
        }
        mock_post.assert_called_with(expected_post_url, headers={'x-yunxiao-token': 'test_token', 'Content-Type': 'application/json'}, json=expected_data, verify=False)

if __name__ == '__main__':
    main()
