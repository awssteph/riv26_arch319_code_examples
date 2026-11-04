# ===========================================================================
# DEMO SNIPPET 6 - Where the EKS backend connects to Bedrock
# ===========================================================================
# Teaching artefact only - NOT imported or run. These are faithful excerpts of
# the real backend code (the service that runs in the EKS pods) so the audience
# can see WHERE model selection and the prompt live, and therefore where the
# Bedrock cost levers actually apply.
#
# Source files (in ../../example-ai-application/source/backend/):
#   - s2s_session_manager.py  -> Nova Sonic speech-to-speech (primary use)
#   - restapi.py              -> text chat (Claude) + Knowledge Base retrieval
#
# HOW EKS REACHES BEDROCK (no keys in code):
#   The backend pod gets AWS credentials via EKS Pod Identity. In the template,
#   BackendPodIdentityAssociation binds the service account
#   `${StackName}-backend-sa` to BackendServiceAccountRole, whose
#   BackendBedrockAccess policy grants bedrock:InvokeModel /
#   InvokeModelWithResponseStream / ApplyGuardrail / Retrieve. So the chain is:
#       EKS pod -> Pod Identity -> IAM role -> bedrock-runtime / bedrock-agent-runtime
#
# BEDROCK IS USED THREE WAYS - and each has a cost lever:
#   1. Nova Sonic streaming (voice)  -> billed on audio in/out tokens. Dominant
#      cost. Lever: trim the big system prompt sent on every session.
#   2. Claude text chat              -> billed on input/output text tokens.
#      Lever: condense the prompt; a Guardrail is attached (quality, not cost).
#   3. KB retrieve (RAG)             -> pulls N chunks into context each call.
#      Lever: tune numberOfResults so you don't over-stuff the prompt.
# ===========================================================================


# ---------------------------------------------------------------------------
# 1. MODEL SELECTION - Nova Sonic (speech-to-speech)
#    s2s_session_manager.py :: S2sSessionManager.__init__
#    The model id is chosen here. restapi.py passes it in when it opens a
#    session (model_id='amazon.nova-2-sonic-v1:0').
# ---------------------------------------------------------------------------
class S2sSessionManager:
    """Manages bidirectional streaming with AWS Bedrock using asyncio"""

    def __init__(self, model_id='amazon.nova-2-sonic-v1:0', region='us-east-1',
                 session_id=None, user_info=None):
        # MODEL SELECTION lever: swapping the Nova Sonic model/tier here changes
        # the per-audio-token rate. Right-size the model to the task before
        # trimming anything else.
        self.model_id = model_id
        self.region = region
        # ... (client built in _initialize_client below)


# ---------------------------------------------------------------------------
# 1a. THE PROMPT - Nova Sonic system prompt (sent on EVERY session)
#     s2s_session_manager.py :: module-level SYSTEM_PROMPT
#     This is the single biggest token lever for the voice path: it is a long
#     block shipped with every session. Condensing it cuts input tokens on
#     every single conversation. (Excerpt - the real prompt is ~70 lines.)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """<{randomized}> You are a professional retail store assistant focused on efficiency and accuracy. Your responses will be formatted in markdown.
Keep your responses helpful, accurate and concise. NEVER ask for the userId, email address or sessionId of the user.
...
Context:
- Current User: {userId} (userId and email)
- Current Session ID: {sessionId}
- Current Date: {date}

Available Tools:
1. Knowledge & Information:
  - search_knowledge_database (FAQ search across standard operating procedures)
  ...
</{randomized}>
"""
# PROMPT lever: every line here is input tokens x every session. Tighten the
# wording, drop redundant guidance, and keep the dynamic Context block small.
# VALIDATE: A/B the trimmed prompt vs the original on a fixed question set -
# compare token counts AND answer quality / tool-calling accuracy.


# ---------------------------------------------------------------------------
# 1b. THE CONNECTION - Bedrock runtime client (how the pod talks to Bedrock)
#     s2s_session_manager.py :: S2sSessionManager._initialize_client
#     Note: NO static keys. In-cluster it resolves container (Pod Identity)
#     credentials. This is the EKS -> Bedrock connection point.
# ---------------------------------------------------------------------------
def _initialize_client(self):
    """Initialize the Bedrock client (simplified from the real method)."""
    # In the EKS pod this branch runs: credentials come from the Pod Identity
    # agent via the container credentials endpoint - not from code.
    if ('AWS_CONTAINER_CREDENTIALS_RELATIVE_URI' in os.environ
            or 'AWS_CONTAINER_CREDENTIALS_FULL_URI' in os.environ):
        http_client = AIOHTTPClient(client_config=AIOHTTPClientConfig())
        credentials_resolver = ContainerCredentialsResolver(http_client)
        config = Config(
            endpoint_uri=f"https://bedrock-runtime.{self.region}.amazonaws.com",
            region=self.region,
            aws_credentials_identity_resolver=credentials_resolver,
        )
    # ... (other branches: user-scoped creds, env creds)

    # The actual Bedrock runtime client used for Nova Sonic streaming:
    self.bedrock_client = BedrockRuntimeClient(config=config)


# ---------------------------------------------------------------------------
# 2. MODEL SELECTION + GUARDRAIL - Claude text chat
#    restapi.py :: module-level config
#    The text path reads the model id from an env var (set by the template /
#    deploy) and attaches a Guardrail on every call.
# ---------------------------------------------------------------------------
BEDROCK_MODEL_ID = os.getenv('BEDROCK_MODEL_ID',
                             'us.anthropic.claude-3-5-haiku-20241022-v1:0')
GUARDRAIL_IDENTIFIER = os.getenv('BD_GUARDRAIL_IDENTIFIER', 'default_identifier')
GUARDRAIL_VERSION = os.getenv('BD_GUARDRAIL_VERSION', 'DRAFT')
# MODEL SELECTION lever: BEDROCK_MODEL_ID is the one knob the TEMPLATE exposes
# (BedrockModelId parameter). Picking a right-sized model (e.g. Haiku vs a
# larger model) is a direct per-token cost change.
# NOTE: the Guardrail is a SAFETY/QUALITY control, not a cost lever - keep it on.


# ---------------------------------------------------------------------------
# 3. RAG RETRIEVAL - Knowledge Base search (how much context we stuff)
#    restapi.py :: KnowledgeBase.kb_search
#    Each call pulls `numberOfResults` chunks of the SOP docs into the prompt.
# ---------------------------------------------------------------------------
def kb_search(self, user_question):
    numberOfResults = 3   # <-- RAG lever: more results = more context tokens
    response = self.bedrock_agent_client.retrieve(
        retrievalQuery={'text': user_question},
        knowledgeBaseId=self.kb_id,
        retrievalConfiguration={
            'vectorSearchConfiguration': {
                'numberOfResults': numberOfResults,
                'overrideSearchType': "HYBRID",
            }
        },
    )
    # RAG lever: numberOfResults directly sets how many chunks get concatenated
    # into the model context. Too high = wasted input tokens + noise; too low =
    # weaker grounding. Tune it against answer quality.
    # VALIDATE: confirm the guardrail contextual-grounding / relevance
    # thresholds still pass after lowering it.


# ===========================================================================
# SUMMARY - the three Bedrock cost levers and where they live
# ---------------------------------------------------------------------------
#   Lever                         Where (file :: symbol)                 Infra?
#   ----------------------------  -------------------------------------  ------
#   Nova Sonic model/tier         s2s_session_manager.py :: __init__     no*
#   Nova Sonic system prompt      s2s_session_manager.py :: SYSTEM_PROMPT no
#   Claude model (right-size)     restapi.py :: BEDROCK_MODEL_ID         yes
#                                 (template BedrockModelId parameter)
#   KB numberOfResults            restapi.py :: kb_search                no
#
#   * model id is passed from code today; only the Claude text model is wired
#     to a template parameter. The big wins (prompt size, numberOfResults) are
#     RUNTIME/CODE changes, not CloudFormation - a good "not every optimisation
#     is an infra change" teaching point.
#
# Observability: Bedrock invocation metrics / model invocation logging in
# CloudWatch + Cost Explorer for the before/after token delta.
# ===========================================================================
