import subprocess

def generate_tree():
    command = "tree /f /a > tree.txt"
    subprocess.run(command, shell=True, check=True)
    print("tree.txt has been generated successfully.")

if __name__ == "__main__":
    generate_tree()