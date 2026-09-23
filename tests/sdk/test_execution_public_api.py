import src.sdk as public_api
import src.sdk.execution_kernel as execution_kernel
import src.sdk.execution_models as execution_models
import src.sdk.execution_store as execution_store
import src.sdk.postgres_receipt_store as postgres_receipt_store


def test_execution_contract_is_publicly_exported() -> None:
    assert public_api.ExecutionKernel is execution_kernel.ExecutionKernel
    assert public_api.EffectState is execution_models.EffectState
    assert public_api.ExecutionCompletion is execution_models.ExecutionCompletion
    assert public_api.ExecutionEvent is execution_models.ExecutionEvent
    assert public_api.ExecutionRequest is execution_models.ExecutionRequest
    assert public_api.ExecutorState is execution_models.ExecutorState
    assert public_api.Observation is execution_models.Observation
    assert public_api.Outcome is execution_models.Outcome
    assert public_api.Receipt is execution_models.Receipt
    assert public_api.ReceiptStore is execution_store.ReceiptStore
    assert public_api.SQLiteReceiptStore is execution_store.SQLiteReceiptStore
    assert public_api.PostgresReceiptStore is postgres_receipt_store.PostgresReceiptStore
    assert public_api.VerificationState is execution_models.VerificationState
