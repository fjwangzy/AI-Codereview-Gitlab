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
        self.yunxiao_url = 'http://yunxiao'

        # 创建PushHandler实例
        self.handler = PushHandler(self.sample_webhook_data, self.yunxiao_token, self.yunxiao_url)

    def test_get_push_commits(self):
        commits = self.handler.get_push_commits()
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0]['message'], 'feat: initial commit')

    @patch('requests.get')
    def test_get_parent_commit_id(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{'parent_ids': ['parent_commit']}]
        mock_get.return_value = mock_response

        parent_id = self.handler.get_parent_commit_id('commit1')
        self.assertEqual(parent_id, 'parent_commit')


class TestYunxiaoMergeRequestHandler(TestCase):
    def setUp(self):
        self.sample_webhook_data = {
            'object_kind': 'merge_request',
            'object_attributes': {
                'iid': 1,
                'target_project_id': 123,
                'action': 'open',
                'target_branch': 'master'
            },
            'aliyun_pk': 'test_pk'
        }
        self.yunxiao_token = 'test_token'
        self.yunxiao_url = 'http://yunxiao'
        self.handler = MergeRequestHandler(self.sample_webhook_data, self.yunxiao_token, self.yunxiao_url)

    @patch('requests.get')
    def test_get_merge_request_changes(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'changes': [{'new_path': 'test.py', 'diff': '+ print("hello")'}]}
        mock_get.return_value = mock_response

        changes = self.handler.get_merge_request_changes()
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['new_path'], 'test.py')

if __name__ == '__main__':
    main()
