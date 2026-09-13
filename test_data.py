from reportlab.pdfgen import canvas
import os

os.makedirs("test_data", exist_ok=True)

# 1. Syllabus
c = canvas.Canvas("test_data/syllabus.pdf")
c.drawString(100, 800, "Syllabus: Data Structures and Algorithms")
c.drawString(100, 780, "Module 1: Arrays and Linked Lists")
c.drawString(100, 760, "Module 2: Stacks and Queues. A stack uses LIFO (Last In First Out).")
c.drawString(100, 740, "A queue uses FIFO (First In First Out).")
c.drawString(100, 720, "Module 3: Trees and Graphs. BFS explores level by level, DFS explores branch by branch.")
c.save()

# 2. Question Paper
c = canvas.Canvas("test_data/question_paper.pdf")
c.drawString(100, 800, "Internal Assessment 1 - Data Structures and Algorithms")
c.drawString(100, 760, "Q1(a) Define a stack and explain its applications. [4]")
c.drawString(100, 740, "Q1(b) Differentiate between stack and queue with suitable examples. [4]")
c.drawString(100, 720, "Q2(a) Explain binary search and analyze its time complexity. [6]")
c.drawString(100, 700, "Q2(b) Compare BFS and DFS with suitable use cases. [6]")
c.save()

# 3. Answer Sheet
c = canvas.Canvas("test_data/answer_sheet.pdf")
c.drawString(100, 800, "Student Name: John Doe")
c.drawString(100, 760, "Answer to 1(a):")
c.drawString(100, 740, "A stack is a linear data structure that follows the LIFO principle.")
c.drawString(100, 720, "Applications include undo mechanisms in text editors and recursive function calls.")
c.drawString(100, 680, "Answer to 1(b):")
c.drawString(100, 660, "Stack follows LIFO whereas Queue follows FIFO.")
c.drawString(100, 640, "For example, plates stacked in a cafeteria represent a stack.")
c.drawString(100, 620, "People waiting in a line for a ticket represent a queue.")
c.save()

print("Test data generated successfully in test_data/")
