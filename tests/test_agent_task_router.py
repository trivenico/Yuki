import unittest
from core.agent_task_router import recommend_task_route
from core.model_capabilities import ModelCapability, ModelCapabilityRegistry

class FakeCoordinator:
 def __init__(self, agents): self.agents=agents; self.calls=[]
 def list_agents(self, *, actor_id):
  self.calls.append(actor_id)
  return self.agents

class AgentTaskRouterTests(unittest.TestCase):
 def setUp(self):
  self.coordinator=FakeCoordinator([
   {"agent_id":"developer","name":"Developer","capabilities":["coding","reasoning"],"enabled":True},
   {"agent_id":"disabled-vision","name":"Vision","capabilities":["vision"],"enabled":False},
   {"agent_id":"research","name":"Research","capabilities":["reasoning"],"enabled":True},
  ])
  self.models=ModelCapabilityRegistry([
   ModelCapability("local/coder",frozenset({"coding","reasoning"}),"local evaluation",True,"verified"),
   ModelCapability("local/unverified",frozenset({"coding"}),"unreviewed",False,"unknown"),
  ])
 def test_recommends_matching_agent_and_verified_model_without_dispatch(self):
  result=recommend_task_route("Debug this Python function",actor_id="owner",
    coordinator=self.coordinator,model_registry=self.models)
  self.assertEqual([a["agent_id"] for a in result["specialist_candidates"]],["developer"])
  self.assertEqual([m["model_id"] for m in result["model_recommendations"]["candidates"]],["local/coder"])
  self.assertTrue(result["requires_user_approval"])
  self.assertFalse(result["dispatch_performed"])
  self.assertFalse(result["model_activated"])
 def test_multi_capability_requires_agent_with_all_capabilities(self):
  result=recommend_task_route("Inspect this screenshot and fix the code shown",
    actor_id="owner",coordinator=self.coordinator)
  self.assertEqual(result["specialist_candidates"],[])
  self.assertIn("vision",result["classification"]["required_capabilities"])
  self.assertIn("coding",result["classification"]["required_capabilities"])
 def test_ambiguous_request_is_not_auto_routed(self):
  result=recommend_task_route("Please help me",actor_id="owner",coordinator=self.coordinator)
  self.assertTrue(result["needs_clarification"])
  self.assertEqual(result["next_action"],"clarify_task")
  self.assertFalse(result["dispatch_performed"])
 def test_coordinator_authorization_is_used(self):
  recommend_task_route("debug this Python function",actor_id="owner",coordinator=self.coordinator)
  self.assertEqual(self.coordinator.calls,["owner"])
 def test_route_includes_reasoning_budget_for_coding_task(self):
  result=recommend_task_route("Debug this Python function",actor_id="owner",
    coordinator=self.coordinator)
  budget=result["reasoning_budget"]
  self.assertEqual(budget["profile"],"high")
  self.assertEqual(budget["max_model_turns"],10)
  self.assertEqual(budget["max_tool_calls"],20)

 def test_device_limits_reduce_route_budget(self):
  result=recommend_task_route("Debug this Python function",actor_id="owner",
    coordinator=self.coordinator,device_limits={"max_model_turns":2,"max_tool_calls":3})
  self.assertEqual(result["reasoning_budget"]["max_model_turns"],2)
  self.assertEqual(result["reasoning_budget"]["max_tool_calls"],3)

 def test_invalid_effort_is_rejected_before_route_recommendation(self):
  with self.assertRaisesRegex(ValueError,"unsupported_reasoning_effort"):
   recommend_task_route("Debug this Python function",actor_id="owner",
    coordinator=self.coordinator,requested_effort="unlimited")

 def test_rejects_invalid_inputs(self):
  with self.assertRaises(ValueError):
   recommend_task_route(" ",actor_id="owner",coordinator=self.coordinator)
  with self.assertRaises(TypeError):
   recommend_task_route("debug code",actor_id="owner",coordinator=object())

if __name__=="__main__": unittest.main()
