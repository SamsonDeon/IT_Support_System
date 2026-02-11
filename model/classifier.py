from sklearn.naive_bayes import MultinomialNB
from sklearn.feature_extraction.text import CountVectorizer

# Training data (simple but effective)
training_data = [
    ("internet not working", "Network"),
    ("slow internet", "Network"),
    ("wifi disconnected", "Network"),

    ("printer not printing", "Printer"),
    ("printer offline", "Printer"),
    ("paper jam in printer", "Printer"),

    ("computer not starting", "Computer"),
    ("pc very slow", "Computer"),
    ("screen not turning on", "Computer"),

    ("cannot login email", "Email"),
    ("email not sending", "Email"),
    ("password reset email", "Email"),

    ("excel not opening", "Software"),
    ("software crashing", "Software"),
    ("system error message", "Software")
]

texts = [item[0] for item in training_data]
labels = [item[1] for item in training_data]

vectorizer = CountVectorizer()
X = vectorizer.fit_transform(texts)

model = MultinomialNB()
model.fit(X, labels)

def classify_issue(issue_description):
    issue_vector = vectorizer.transform([issue_description.lower()])
    return model.predict(issue_vector)[0]
