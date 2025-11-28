import sys
import os

# Add the current directory to sys.path
sys.path.append(os.getcwd())

try:
    print("Importing resources/gasto_resource.py...")
    from resources import gasto_resource
    print("Success!")

    print("Importing resources/pago_resource.py...")
    from resources import pago_resource
    print("Success!")

    print("Importing resources/venta_resource.py...")
    from resources import venta_resource
    print("Success!")

    print("All resources imported successfully. No syntax errors.")
except Exception as e:
    print(f"Error importing resources: {e}")
    sys.exit(1)
