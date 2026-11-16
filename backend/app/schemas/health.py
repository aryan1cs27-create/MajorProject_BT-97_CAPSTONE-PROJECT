"""Pydantic response schemas for the health endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    app: str = Field(examples=["MatchWise"])
    version: str = Field(examples=["0.1.0"])
    environment: str = Field(examples=["development"])


class DatabaseStatus(BaseModel):
    connected: bool = Field(examples=[True])
    detail: str = Field(examples=["Connection OK"])


class DatabaseHealthResponse(BaseModel):
    status: str = Field(examples=["ok", "error"])
    database: DatabaseStatus
