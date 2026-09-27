import unittest
import json
from types import SimpleNamespace
from scripts.build_shortcut import build_with_shortcutkit, integration_blueprint, validate_document


class BuilderTests(unittest.TestCase):
    def check(self, actions):
        shortcut = build_with_shortcutkit('test', {'actions': actions})
        self.assertEqual(validate_document(shortcut.to_plist(), len(shortcut.actions)), len(shortcut.actions))
        return shortcut.to_plist()['WFWorkflowActions']

    def test_simple_and_flow(self):
        rows = self.check([{'action': 'Ask for Input', 'id': 'answer', 'parameters': {'question': 'Name?', 'type': 'Text'}},
                           {'action': 'Show Notification', 'parameters': {'Body': {'$text': ['Hi ', {'$ref': 'answer'}]}}}])
        self.assertEqual(rows[1]['WFWorkflowActionParameters']['WFNotificationActionBody']['Value']['string'], 'Hi \ufffc')

    def test_conditional_integration(self):
        shortcut = build_with_shortcutkit('integration', integration_blueprint())
        rows = shortcut.to_plist()['WFWorkflowActions']
        self.assertEqual(validate_document(shortcut.to_plist(), 7), 7)
        self.assertEqual([r['WFWorkflowActionParameters'].get('WFControlFlowMode') for r in rows if r['WFWorkflowActionIdentifier'].endswith('conditional')], [0, 1, 2])
        self.assertEqual(rows[2]['WFWorkflowActionParameters']['WFCondition'], 99)

    def test_loops_menus_lists(self):
        self.check([{'action': 'List', 'id': 'items', 'parameters': {'Items': ['one', 'two']}},
                    {'repeat': {'items': {'$ref': 'items'}}, 'actions': [{'action': 'Show Notification', 'parameters': {'Body': 'inside'}}]},
                    {'repeat': {'count': 2}, 'actions': [{'action': 'Show Notification'}]},
                    {'menu': {'prompt': 'Choose', 'options': {'A': [{'action': 'Show Notification'}], 'B': [{'action': 'Show Notification'}]}}}])

    def test_dictionary_and_app_intent(self):
        from shortcutkit import ACTIONS
        app = next(k for k, v in ACTIONS.items() if v.get('descriptor') and not v.get('params'))
        rows = self.check([{'action': 'Dictionary', 'parameters': {'Items': {'title': 'Hello', 'count': 3, 'enabled': True}}},
                           {'identifier': app}])
        items = rows[0]['WFWorkflowActionParameters']['WFItems']['Value']['WFDictionaryFieldValueItems']
        self.assertEqual([i['WFItemType'] for i in items], [0, 3, 4])
        self.assertIn('AppIntentDescriptor', rows[1]['WFWorkflowActionParameters'])

    def test_reject_unknown(self):
        with self.assertRaises(ValueError): self.check([{'action': 'invented.action'}])
        with self.assertRaises(ValueError): self.check([{'action': 'Show Notification', 'parameters': {'Imaginary': 1}}])
        with self.assertRaises(ValueError): self.check([{'action': 'Show Notification', 'parameters': {'Body': {'$ref': 'future'}}}])

    def test_planner_search_and_blueprint(self):
        from scripts.plan_with_ai import plan, search_actions
        self.assertTrue(any(x['identifier'] == 'is.workflow.actions.notification' for x in search_actions('show notification')))
        call = SimpleNamespace(type='function_call', name='search_actions', arguments='{"query":"notification"}', call_id='one',
                               model_dump=lambda **kw: {'type':'function_call','name':'search_actions','arguments':'{"query":"notification"}','call_id':'one'})
        first = SimpleNamespace(output=[call])
        second = SimpleNamespace(output=[], output_text=json.dumps(integration_blueprint()))
        class Client:
            def __init__(self): self.calls = []
            class responses:
                queue = [first, second]
                @classmethod
                def create(cls, **kwargs):
                    assert kwargs['store'] is False
                    return cls.queue.pop(0)
        self.check(plan('Ask for text and check work', client=Client())['actions'])


if __name__ == '__main__': unittest.main()
