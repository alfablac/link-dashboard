# main.py  
import os  
import logging  
from app import app, worker_thread  

if __name__ == "__main__":  
    # Configure logging  
    logging.basicConfig(  
        level=logging.INFO,  
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',  
        handlers=[  
            logging.FileHandler("link_system.log"),  
            logging.StreamHandler()  
        ]  
    )  
    logger = logging.getLogger(__name__)  
    
    logger.info("Starting Link Access Dashboard System")  
    
    # Start the worker thread  
    worker_thread.start()  
    logger.info("Background worker started")  
    
    # Run the Flask application  
    port = int(os.environ.get('PORT', 17545))
    app.run(host='0.0.0.0', port=port, debug=False)