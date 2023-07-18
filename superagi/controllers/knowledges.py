from fastapi_sqlalchemy import db
from fastapi import HTTPException, Depends, Query, status
from fastapi import APIRouter
from superagi.config.config import get_config
from superagi.helper.auth import get_user_organisation
from superagi.models.knowledges import Knowledges
from superagi.models.marketplace_stats import MarketPlaceStats
from superagi.models.knowledge_configs import KnowledgeConfigs
from superagi.models.vector_db_indices import VectordbIndices
from superagi.models.vector_dbs import Vectordbs
from superagi.helper.s3_helper import S3Helper
from superagi.models.vector_db_configs import VectordbConfigs
from superagi.vector_store.vector_factory import VectorFactory
from superagi.vector_embeddings.vector_embedding_factory import VectorEmbeddingFactory

router = APIRouter()

@router.get("/get/list")
def get_knowledge_list(
    page: int = Query(None, title="Page Number"),
    organisation = Depends(get_user_organisation)
):
    """
    Get Marketplace Knowledge list.

    Args:
        page (int, optional): The page number for pagination. Defaults to None.

    Returns:
        dict: The response containing the marketplace list.

    """

    marketplace_knowledges = Knowledges.fetch_marketplace_list(page)
    marketplace_knowledges_with_install = Knowledges.get_knowledge_install_details(db.session, marketplace_knowledges, organisation)
    for knowledge in marketplace_knowledges_with_install:
        knowledge["install_number"] = MarketPlaceStats.get_knowledge_installation_number(knowledge["id"])
    return marketplace_knowledges_with_install    

@router.get("/marketplace/list/{page}")
def get_marketplace_knowledge_list(page: int = 0):
    organisation_id = int(get_config("MARKETPLACE_ORGANISATION_ID"))
    page_size = 30

    # Apply search filter if provided
    query = db.session.query(Knowledges).filter(Knowledges.organisation_id == organisation_id)

    if page < 0:
        knowledges = query.all()
    # Paginate the results
    knowledges = query.offset(page * page_size).limit(page_size).all()

    return knowledges

@router.get("/user/list")
def get_user_knowledge_list(organisation = Depends(get_user_organisation)):
    marketplace_knowledges = Knowledges.fetch_marketplace_list(page=-1)
    user_knowledge_list = Knowledges.get_organisation_knowledges(db.session, organisation)
    for user_knowledge in user_knowledge_list:
        if user_knowledge["name"] in [knowledge.name for knowledge in marketplace_knowledges]:
            user_knowledge["is_marketplace"] = True
        else:
            user_knowledge["is_marketplace"] = False
    return user_knowledge_list

@router.get("/marketplace/get/details/{knowledge_name}")
def get_knowledge_details(knowledge_name: str):
    knowledge_data = Knowledges.fetch_knowledge_details_marketplace(knowledge_name)
    knowledge_config_data = KnowledgeConfigs.fetch_knowledge_config_details_marketplace(knowledge_data["id"])
    knowledge_config_data["introduction"] = eval(knowledge_config_data["introduction"])
    # knowledge_config_data["use_cases"] = eval(knowledge_config_data["use_cases"])
    knowledge_data_with_config = knowledge_data | knowledge_config_data
    knowledge_data_with_config["install_number"] = MarketPlaceStats.get_knowledge_installation_number(knowledge_data_with_config["id"])
    return knowledge_data_with_config

@router.get("/marketplace/details/{knowledge_name}")
def get_marketplace_knowledge_details(knowledge_name: str):
    organisation_id = int(get_config("MARKETPLACE_ORGANISATION_ID"))
    knowledge_details = db.session.query(Knowledges).filter(Knowledges.name == knowledge_name, Knowledges.organisation_id == organisation_id).first()
    return knowledge_details

@router.get("/user/get/details/{knowledge_id}")
def get_user_knowledge_details(knowledge_id: int):
    knowledge_data = Knowledges.get_knowledge_from_id(db.session, knowledge_id)
    vector_database_index = VectordbIndices.get_vector_index_from_id(db.session, knowledge_data.vector_db_index_id)
    vector_database = Vectordbs.get_vector_db_from_id(db.session, vector_database_index.vector_db_id)
    knowledge = {
        "name": knowledge_data.name,
        "description": knowledge_data.description,
        "vector_database_index": {
            "id": vector_database_index.id,
            "name": vector_database_index.name
        },
        "vector_database": vector_database.name,
        "installation_type": vector_database_index.state
    }
    return knowledge

@router.post("/add_or_update/data")
def add_update_user_knowledge(knowledge_data: dict, organisation = Depends(get_user_organisation)):
    knowledge_data["organisation_id"] = organisation.id
    knowledge_data["contributed_by"] = organisation.name
    knowledge = Knowledges.add_update_knowledges(db.session, knowledge_data)
    return {"success": True, "id": knowledge.id}

@router.post("/delete/{knowledge_id}")
def delete_user_knowledge(knowledge_id: int):
    try:
        Knowledges.delete_knowledge(db.session, knowledge_id)
        return {"success": True}
    except:
        return f"No Knowledge found for {knowledge_id}."

@router.get("/install/{knowledge_name}/index/{vector_db_index_id}")
def install_selected_knowledge(knowledge_name: str, vector_db_index_id: int, organisation = Depends(get_user_organisation)):
    vector_db_index = VectordbIndices.get_vector_index_from_id(db.session, vector_db_index_id)
    selected_knowledge = Knowledges.fetch_knowledge_details_marketplace(knowledge_name)
    selected_knowledge_config = KnowledgeConfigs.fetch_knowledge_config_details_marketplace(selected_knowledge['id'])
    file_chunks = S3Helper().get_json_file(selected_knowledge_config["file_path"])
    vector = Vectordbs.get_vector_db_from_id(db.session, vector_db_index.vector_db_id)
    db_creds = VectordbConfigs.get_vector_db_config_from_db_id(db.session, vector.id)
    upsert_data = VectorEmbeddingFactory.convert_final_chunks_to_embeddings(vector.db_type, file_chunks)
    upsert_data_with_creds = {"embeddings": upsert_data, "creds": db_creds}
    try:
        VectorFactory.add_embeddings_to_vector_store(vector.db_type, vector_db_index.name, **upsert_data_with_creds)
    except:
        return {"success": False}
    selected_knowledge_data = {
        "id": -1,
        "name": selected_knowledge["name"],
        "description": selected_knowledge["description"],
        "index_id": vector_db_index_id,
        "organisation_id": organisation.id,
        "contributed_by": selected_knowledge["contributed_by"],
    }
    new_knowledge = Knowledges.add_update_knowledges(db.session, selected_knowledge_data)
    selected_knowledge_config.pop("file_path")
    configs = selected_knowledge_config
    KnowledgeConfigs.add_update_knowledge_config(db.session, new_knowledge.id, configs)
    VectordbIndices.update_vector_index_state(db.session, vector_db_index_id, "Marketplace")
    install_number = MarketPlaceStats.get_knowledge_installation_number(selected_knowledge["id"])
    MarketPlaceStats.update_knowledge_install_number(db.session, selected_knowledge["id"], int(install_number) + 1)
    return {"success": True}

@router.post("/uninstall/{knowledge_name}")
def uninstall_selected_knowledge(knowledge_name: str, organisation = Depends(get_user_organisation)):
    knowledge = db.session.query(Knowledges).filter(Knowledges.name == knowledge_name, Knowledges.organisation_id == organisation.id).first()
    knowledge_config = KnowledgeConfigs.get_knowledge_config_from_knowledge_id(db.session, knowledge.id)
    vector_ids = eval(knowledge_config["vector_ids"])
    vector_db_index = VectordbIndices.get_vector_index_from_id(db.session, knowledge.vector_db_index_id)
    vector = Vectordbs.get_vector_db_from_id(db.session, vector_db_index.vector_db_id)
    db_creds = VectordbConfigs.get_vector_db_config_from_db_id(db.session, vector.id)
    try:
        VectorFactory.delete_embeddings_from_vector_store(vector.db_type,vector_db_index.name, vector_ids, **db_creds)
    except:
        return {"success": False}
    KnowledgeConfigs.delete_knowledge_config(db.session, knowledge.id)
    Knowledges.delete_knowledge(db.session, knowledge.id)
    return {"success": True}