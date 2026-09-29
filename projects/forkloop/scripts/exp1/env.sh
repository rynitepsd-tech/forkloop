# source on forkloop-main for exp1 (no secrets here; the key file is separate)
source ~/.config/forkloop/env
export FORKLOOP_SESSION_LEDGER=/home/ubuntu/programs/ledger-main.sqlite
export FORKLOOP_DOCKER_IMAGE=forkloop/claims-ops-v1:3
export FORKLOOP_DOCKER_CONCURRENCY=96
export FORKLOOP_POOL_LOG=0
export FORKLOOP_REGISTRY=/home/ubuntu/programs/resources.jsonl
export PATH=/home/ubuntu/venvs/forkloop/bin:$PATH
FAMS=reschedule_constrained,update_insurance_reconcile,resolve_denial,compose_claims
cd /home/ubuntu/repo/projects/forkloop
