from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class SourceEnum(str, Enum):
  genbank = "genbank"
  gencode = "gencode"

class ProteinTranslator(SQLModel, table=True):
  id: Optional[int] = Field(default=None, primary_key=True)
  source: SourceEnum
  sequence: str
  organism: str | None
  target_protein: str
  hash_id: str = Field(unique=True, index=True)