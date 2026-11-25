"""POST /api/assets/model/plan (contract section 11): one sentence -> parametric AssetSpec.

The spec is only written and statically checked, not built or stored; the UI previews it and
then calls A's POST /api/assets/model/build. main.py includes `router` at integration.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import plan

router = APIRouter()


class PlanBody(BaseModel):
    description: str = Field(..., min_length=1, max_length=500)


@router.post("/api/assets/model/plan")
def model_plan(body: PlanBody):  # sync def: FastAPI runs it in a worker thread
    desc = body.description.strip()
    if not desc:
        raise HTTPException(422, "description is empty")
    try:
        return plan.model_plan(desc)
    except (ValueError, RuntimeError) as e:  # model cut off, Bedrock error text
        raise HTTPException(502, f"规格生成失败：{e}"[:500]) from e
